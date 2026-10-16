from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import main
from app.main import assigned_student_ids, create_student, delete_import, delete_test, question_media, student_can_access, student_classes, student_tests, submit, test_analytics as assessment_analytics
from app.models import AccountStatus, Attempt, Classroom, ClassMembership, ImportJob, ImportStatus, Question, QuestionKind, Response as AnswerResponse, Role, SourceDocument, Test as Assessment, TestClassAssignment as ClassAssignment, TestStatus as AssessmentStatus, TestVersion as AssessmentVersion, User
from app.tasks import finalize_expired_attempts
from app.schemas import LoginIn, RosterUpdate, StudentCreate
from fastapi import HTTPException

def make_db():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()

def test_class_assignment_controls_visibility_and_analytics():
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    first=User(id="student-1",name="One",email="one@example.com",password_hash="x",role=Role.STUDENT)
    second=User(id="student-2",name="Two",email="two@example.com",password_hash="x",role=Role.STUDENT)
    test=Assessment(id="test",owner_id="teacher",title="Physics",subject="Physics",status=AssessmentStatus.PUBLISHED,duration_seconds=3600,max_attempts=1,results_policy={"mode":"immediate"})
    classroom=Classroom(id="class",owner_id="teacher",name="12A")
    version=AssessmentVersion(id="version",test_id="test",ordinal=1,content={"questions":[]},published_at=datetime.now(timezone.utc))
    test.published_version_id="version"
    db.add_all([teacher,first,second,test,classroom,version,ClassMembership(class_id="class",student_id="student-1"),ClassMembership(class_id="class",student_id="student-2"),ClassAssignment(test_id="test",class_id="class")])
    db.commit()
    assert assigned_student_ids(test,db)=={"student-1","student-2"}
    assert student_can_access(test,"student-1",db)
    assert not student_can_access(test,"not-a-student",db)
    db.add(Attempt(id="attempt",test_id="test",version_id="version",student_id="student-1",started_at=datetime.now(timezone.utc),ends_at=datetime.now(timezone.utc),submitted_at=datetime.now(timezone.utc),score=4))
    db.commit()
    analytics=assessment_analytics("test",teacher,db)
    assert analytics["summary"]=={"assigned":2,"started":1,"submitted":1,"pending":1}
    assert {row["status"] for row in analytics["students"]}=={"submitted","not_started"}
    assessments=student_tests(first,db)
    assert assessments[0]["attempts_remaining"]==0
    assert assessments[0]["latest_attempt"]["id"]=="attempt"
    assert assessments[0]["latest_attempt"]["status"]=="submitted"

def test_student_classes_returns_only_the_current_students_enrolments():
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    first=User(id="student-1",name="One",email="one@example.com",password_hash="x",role=Role.STUDENT)
    second=User(id="student-2",name="Two",email="two@example.com",password_hash="x",role=Role.STUDENT)
    enrolled=Classroom(id="class-1",owner_id="teacher",name="Physics 12A",description="Morning section")
    other=Classroom(id="class-2",owner_id="teacher",name="Physics 12B")
    db.add_all([teacher,first,second,enrolled,other,ClassMembership(class_id="class-1",student_id="student-1"),ClassMembership(class_id="class-2",student_id="student-2")])
    db.commit()

    assert student_classes(first,db)==[{"id":"class-1","name":"Physics 12A","description":"Morning section"}]

def test_teacher_can_delete_an_unattempted_test_and_its_assignments():
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    test=Assessment(id="test",owner_id=teacher.id,title="Physics",subject="Physics",status=AssessmentStatus.DRAFT,duration_seconds=3600,max_attempts=1,results_policy={"mode":"immediate"})
    classroom=Classroom(id="class",owner_id=teacher.id,name="12A")
    version=AssessmentVersion(id="version",test_id=test.id,ordinal=1,content={"questions":[]})
    db.add_all([teacher,test,classroom,version,ClassAssignment(test_id=test.id,class_id=classroom.id)])
    db.commit()
    test_id=test.id
    version_id=version.id

    delete_test(test_id,teacher,db)

    assert db.get(Assessment,test_id) is None
    assert db.get(AssessmentVersion,version_id) is None
    assert not db.query(ClassAssignment).filter_by(test_id=test_id).count()

def test_teacher_can_delete_a_finished_import(monkeypatch):
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    source=SourceDocument(id="source",owner_id=teacher.id,filename="paper.pdf",object_key="documents/teacher/paper.pdf",sha256="a" * 64,media_type="application/pdf",size_bytes=10)
    job=ImportJob(id="job",owner_id=teacher.id,question_document_id=source.id,status=ImportStatus.FAILED)
    question=Question(id="question",owner_id=teacher.id,import_job_id=job.id,kind=QuestionKind.SINGLE,stem_markdown="Question",options=[{"markdown":"A"},{"markdown":"B"}])
    db.add_all([teacher,source,job,question]);db.commit()
    monkeypatch.setattr(main,"delete_prefix",lambda prefix:None)

    delete_import(job.id,teacher,db)

    assert db.get(ImportJob,"job") is None
    assert db.get(Question,"question") is None

def test_question_media_streams_bytes_without_object_store_redirect(monkeypatch):
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    question=Question(id="question",owner_id="teacher",kind=QuestionKind.SINGLE,stem_markdown="x",options=[{"value":"a","markdown":"a"}],diagrams=[{"object_key":"diagram.png","alt":"Diagram"}])
    db.add_all([teacher,question]);db.commit()
    monkeypatch.setattr(main,"get",lambda key:b"image-bytes")
    response=question_media("question",0,teacher,db)
    assert response.media_type=="image/png"
    assert response.headers["cache-control"]=="private, max-age=300"

def test_seeded_local_email_addresses_are_accepted_for_login_and_rosters():
    assert LoginIn(email="student1@testnow.local",access_code="12345",role="student").email=="student1@testnow.local"
    roster=RosterUpdate(emails=["student1@testnow.local", " STUDENT2@TESTNOW.LOCAL "])
    assert roster.emails==["student1@testnow.local", "student2@testnow.local"]

def test_teacher_cannot_take_over_another_teachers_student():
    db=make_db()
    first=User(id="teacher-1",name="First",email="first@example.com",password_hash="x",role=Role.TEACHER,status=AccountStatus.ACTIVE)
    second=User(id="teacher-2",name="Second",email="second@example.com",password_hash="x",role=Role.TEACHER,status=AccountStatus.ACTIVE)
    student=User(id="student",name="Original",email="student@example.com",password_hash="x",role=Role.STUDENT,status=AccountStatus.ACTIVE)
    first_class=Classroom(id="first-class",owner_id=first.id,name="First")
    second_class=Classroom(id="second-class",owner_id=second.id,name="Second")
    db.add_all([first,second,student,first_class,second_class,ClassMembership(class_id=first_class.id,student_id=student.id)]);db.commit()
    data=StudentCreate(name="Taken over",email=student.email,access_code="12345",class_id=second_class.id)
    try:
        create_student(data,second,db)
        assert False, "expected ownership conflict"
    except HTTPException as exc:
        assert exc.status_code==409
    assert student.name=="Original"
    assert not db.get(ClassMembership,{"class_id":second_class.id,"student_id":student.id})

def test_expired_attempt_is_auto_submitted_with_saved_responses():
    db=make_db()
    student=User(id="student",name="Student",email="student@example.com",password_hash="x",role=Role.STUDENT,status=AccountStatus.ACTIVE)
    test=Assessment(id="test",owner_id="teacher",title="Test",subject="Subject",status=AssessmentStatus.PUBLISHED,duration_seconds=60,max_attempts=1)
    version=AssessmentVersion(id="version",test_id=test.id,ordinal=1,content={"questions":[{"id":"question","kind":"single_choice","answer":{"option":0},"scoring":{"correct":4,"incorrect":-1}}]},published_at=datetime.now(timezone.utc))
    attempt=Attempt(id="attempt",test_id=test.id,version_id=version.id,student_id=student.id,started_at=datetime.now(timezone.utc),ends_at=datetime.now(timezone.utc).replace(year=2020))
    response=AnswerResponse(attempt_id=attempt.id,question_id="question",answer={"option":0})
    db.add_all([student,test,version,attempt,response]);db.commit()
    # Explicit enrolment keeps this test assigned even without a classroom.
    from app.models import Enrollment
    db.add(Enrollment(test_id=test.id,student_id=student.id));db.commit()
    completed=submit(attempt.id,student,db)
    assert completed["submitted_at"] is not None
    assert completed["score"]==4
    assert db.get(Attempt,attempt.id).submitted_at is not None

def test_expiry_worker_finalizes_attempt_without_a_browser(monkeypatch):
    db=make_db()
    student=User(id="student",name="Student",email="student@example.com",password_hash="x",role=Role.STUDENT,status=AccountStatus.ACTIVE)
    test=Assessment(id="test",owner_id="teacher",title="Test",subject="Subject",status=AssessmentStatus.PUBLISHED,duration_seconds=60,max_attempts=1)
    version=AssessmentVersion(id="version",test_id=test.id,ordinal=1,content={"questions":[]},published_at=datetime.now(timezone.utc))
    attempt=Attempt(id="attempt",test_id=test.id,version_id=version.id,student_id=student.id,started_at=datetime.now(timezone.utc),ends_at=datetime.now(timezone.utc).replace(year=2020))
    db.add_all([student,test,version,attempt]);db.commit()
    monkeypatch.setattr("app.tasks.SessionLocal",lambda:db)

    assert finalize_expired_attempts.run()=={"finalized":1}
    assert db.get(Attempt,attempt.id).submitted_at is not None
