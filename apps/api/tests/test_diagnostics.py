from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi import HTTPException

from app.db import Base
from app.diagnostics import classify_skills, ensure_version_labels, summarize, timing_for_attempt
from app.models import Attempt, AttemptEvent, Classroom, ClassMembership, Response, Role, Test as Assessment, TestClassAssignment as BatchAssignment, TestVersion as AssessmentVersion, User


def test_two_missed_questions_are_limited_evidence():
    questions=[{"id":f"q{i}","number":i,"test_id":"test","attempt_id":"attempt","student_id":"student","subject":"Physics","chapter":"Kinematics","topic":"Relative velocity","skill":"Relative velocity in two dimensions","outcome":"incorrect"} for i in (1,2)]
    group=summarize(questions,"skill")[0]
    assert group["finding"]=="Needs attention"
    assert group["confidence"]=="limited evidence"
    assert group["student_count"]==1
    assert group["question_count"]==2


def test_skill_override_is_used_without_provider(monkeypatch):
    monkeypatch.setattr("app.diagnostics.settings",lambda:type("Settings",(),{"openrouter_api_key":None})())
    question={"id":"q","taxonomy":[{"kind":"topic","name":"Relative velocity"}],"interaction":{"diagnostic_skill":"Relative velocity in two dimensions"}}
    assert classify_skills([question])["q"]=="Relative velocity in two dimensions"


def test_legacy_math_version_gets_useful_saved_labels_without_ai(monkeypatch):
    monkeypatch.setattr("app.diagnostics.settings",lambda:type("Settings",(),{"openrouter_api_key":None})())
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        version=AssessmentVersion(id="version",test_id="test",ordinal=1,content={"questions":[{"id":"q1","stem_markdown":"The region bounded by the parabola $y=4-x^2$ and the coordinate axes","answer":{"option":0}},{"id":"q2","stem_markdown":"Find the area between two curves $y=x^3-x$ and $y=x-x^2$","answer":{"option":0}}]})
        db.add(version);db.commit()
        ensure_version_labels(db,version)
        labels=[question["skill"] for question in version.content["questions"]]
        assert labels==["Area bounded by a curve and axes","Area between curves"]
        assert all(any(node["kind"]=="topic" and node["name"]=="Area under curves" for node in question["taxonomy"]) for question in version.content["questions"])
        ensure_version_labels(db,version)
        assert [question["skill"] for question in version.content["questions"]]==labels


def test_timing_deduplicates_intervals_and_caps_total_to_attempt_duration():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        start=datetime.now(timezone.utc)
        attempt=Attempt(id="attempt",test_id="test",version_id="version",student_id="student",started_at=start,ends_at=start+timedelta(seconds=60),submitted_at=start+timedelta(seconds=30),cohort_classes=[])
        db.add(attempt)
        for index,(token,duration) in enumerate((("interval-1",20000),("interval-1",20000),("interval-2",20000))):
            db.add(AttemptEvent(id=f"event-{index}",attempt_id="attempt",event_type="question_focus_interval",data={"question_id":"q","interval_id":token,"duration_ms":duration},created_at=start+timedelta(seconds=index)))
        db.commit()
        timing=timing_for_attempt(db,attempt)
        assert timing["q"]=={"seconds":30.0,"visits":2}
        assert "missing" not in timing


def test_teacher_batch_drilldown_and_student_result_access():
    from app.main import teacher_test_diagnostics, teacher_student_diagnostics, student_diagnostics
    engine=create_engine("sqlite+pysqlite:///:memory:",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        now=datetime.now(timezone.utc)
        questions=[{"id":f"q{i}","kind":"single_choice","stem_markdown":f"Relative velocity question {i}","answer":{"option":0},"scoring":{"correct":4,"incorrect":-1,"unanswered":0},"taxonomy":[{"kind":"subject","name":"Physics"},{"kind":"chapter","name":"Kinematics"},{"kind":"topic","name":"Relative velocity"}],"skill":"Relative velocity in two dimensions"} for i in (1,2)]
        db.add_all([User(id="teacher",name="Teacher",email="teacher@example.com",role=Role.TEACHER),User(id="other",name="Other",email="other@example.com",role=Role.TEACHER),User(id="student",name="Student",email="student@example.com",role=Role.STUDENT),Classroom(id="class",owner_id="teacher",name="Batch A"),Assessment(id="test",owner_id="teacher",title="Kinematics",subject="Physics",duration_seconds=60,results_policy={"mode":"immediate"}),AssessmentVersion(id="version",test_id="test",ordinal=1,content={"questions":questions}),ClassMembership(class_id="class",student_id="student"),BatchAssignment(test_id="test",class_id="class"),Attempt(id="attempt",test_id="test",version_id="version",student_id="student",started_at=now-timedelta(seconds=30),ends_at=now+timedelta(seconds=30),submitted_at=now,score=-2),Response(attempt_id="attempt",question_id="q1",answer={"option":1}),Response(attempt_id="attempt",question_id="q2",answer={"option":1})])
        db.commit()
        batch=teacher_test_diagnostics("test","class",db.get(User,"teacher"),db)
        assert batch["skills"][0]["student_count"]==1
        assert batch["skills"][0]["finding"]=="Needs attention"
        assert teacher_student_diagnostics("student",db.get(User,"teacher"),db)["skills"]
        for call in (lambda:teacher_test_diagnostics("test",None,db.get(User,"other"),db),lambda:teacher_student_diagnostics("student",db.get(User,"other"),db)):
            try:call();assert False,"expected a scope rejection"
            except HTTPException as error:assert error.status_code==404
        assert student_diagnostics(db.get(User,"student"),db)["skills"][0]["incorrect"]==2
    engine.dispose()
