from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import main
from app.main import assigned_student_ids, create_import_test, create_student, delete_import, delete_test, get_attempt, integrity_violation, question_media, questions, save_response, student_can_access, student_classes, student_tests, submit, test_analytics as assessment_analytics
from app.models import AccountStatus, Attempt, AttemptEvent, Classroom, ClassMembership, ImportJob, ImportStatus, Question, QuestionKind, Response as AnswerResponse, Role, SourceDocument, Test as Assessment, TestClassAssignment as ClassAssignment, TestStatus as AssessmentStatus, TestVersion as AssessmentVersion, User
from app.tasks import finalize_expired_attempts
from app.schemas import ImportTestCreate, IntegrityViolationIn, LoginIn, ResponseIn, RosterUpdate, StudentCreate, SubmitIn
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
    assert {key:analytics["summary"][key] for key in ("assigned","started","submitted","pending","cancelled","flagged")}=={"assigned":2,"started":1,"submitted":1,"pending":1,"cancelled":0,"flagged":0}
    assert {row["status"] for row in analytics["students"]}=={"submitted","not_started"}
    assert analytics["batches"][0]["classroom"]["name"]=="12A"
    assert analytics["students"][0]["attempts"][0]["status"]=="submitted"
    assessments=student_tests(first,db)
    assert assessments[0]["attempts_remaining"]==0
    assert assessments[0]["latest_attempt"]["id"]=="attempt"
    assert assessments[0]["latest_attempt"]["status"]=="submitted"

def test_completed_attempt_exposes_the_same_duration_to_student_and_teacher():
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    student=User(id="student",name="Student",email="student@example.com",password_hash="x",role=Role.STUDENT)
    test=Assessment(id="test",owner_id=teacher.id,title="Physics",subject="Physics",status=AssessmentStatus.PUBLISHED,duration_seconds=7200,max_attempts=1,results_policy={"mode":"immediate"})
    version=AssessmentVersion(id="version",test_id=test.id,ordinal=1,content={"questions":[]},published_at=datetime.now(timezone.utc));test.published_version_id=version.id
    started_at=datetime(2025,1,1,10,0,0,tzinfo=timezone.utc)
    attempt=Attempt(id="attempt",test_id=test.id,version_id=version.id,student_id=student.id,started_at=started_at,ends_at=datetime(2025,1,1,12,0,0,tzinfo=timezone.utc),submitted_at=datetime(2025,1,1,11,2,3,tzinfo=timezone.utc),score=0)
    from app.models import Enrollment
    db.add_all([teacher,student,test,version,attempt,Enrollment(test_id=test.id,student_id=student.id)]);db.commit()

    student_attempt=get_attempt(attempt.id,student,db)
    student_dashboard=student_tests(student,db)
    teacher_dashboard=assessment_analytics(test.id,teacher,db)

    assert student_attempt["duration_seconds"]==3723
    assert student_dashboard[0]["latest_attempt"]["duration_seconds"]==3723
    assert teacher_dashboard["students"][0]["duration_seconds"]==3723
    assert teacher_dashboard["students"][0]["attempts"][0]["duration_seconds"]==3723

def test_submit_rejects_a_stale_revision_and_preserves_the_saved_option():
    db=make_db()
    student=User(id="student",name="Student",email="student@example.com",password_hash="x",role=Role.STUDENT,status=AccountStatus.ACTIVE)
    test=Assessment(id="test",owner_id="teacher",title="Test",subject="Subject",status=AssessmentStatus.PUBLISHED,duration_seconds=3600,max_attempts=1)
    question={"id":"question","kind":"single_choice","options":[{"markdown":"A"},{"markdown":"B"}],"answer":{"option":1},"scoring":{"correct":4,"incorrect":-1,"unanswered":0}}
    version=AssessmentVersion(id="version",test_id=test.id,ordinal=1,content={"questions":[question]},published_at=datetime.now(timezone.utc));test.published_version_id=version.id
    attempt=Attempt(id="attempt",test_id=test.id,version_id=version.id,student_id=student.id,started_at=datetime.now(timezone.utc),ends_at=datetime(2030,1,1,tzinfo=timezone.utc))
    db.add_all([student,test,version,attempt]);db.commit()

    saved=save_response(attempt.id,"question",ResponseIn(answer={"option":1},revision=0),student,db)
    assert saved["revision"]==1

    try:
        submit(attempt.id,SubmitIn(revision=0),student,db)
        assert False,"expected stale submission to be rejected"
    except HTTPException as exc:
        assert exc.status_code==409
    assert db.get(Attempt,attempt.id).submitted_at is None
    assert db.get(AnswerResponse,{"attempt_id":attempt.id,"question_id":"question"}).answer=={"option":1}

    completed=submit(attempt.id,SubmitIn(revision=1),student,db)
    assert completed["score"]==4
    assert completed["responses"]["question"]["answer"]=={"option":1}

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

def test_teacher_question_library_filters_approved_questions_by_source_pdf():
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    first_source=SourceDocument(id="source-1",owner_id=teacher.id,filename="worksheet-1.pdf",object_key="documents/teacher/worksheet-1.pdf",sha256="a" * 64,media_type="application/pdf",size_bytes=10)
    second_source=SourceDocument(id="source-2",owner_id=teacher.id,filename="worksheet-2.pdf",object_key="documents/teacher/worksheet-2.pdf",sha256="b" * 64,media_type="application/pdf",size_bytes=10)
    first_job=ImportJob(id="job-1",owner_id=teacher.id,question_document_id=first_source.id,status=ImportStatus.REVIEW)
    second_job=ImportJob(id="job-2",owner_id=teacher.id,question_document_id=second_source.id,status=ImportStatus.REVIEW)
    approved_at=datetime.now(timezone.utc)
    db.add_all([
        teacher,first_source,second_source,first_job,second_job,
        Question(id="approved-first",owner_id=teacher.id,import_job_id=first_job.id,kind=QuestionKind.SINGLE,stem_markdown="First approved",options=[{"markdown":"4"},{"markdown":"5"}],answer={"option":0},approved_at=approved_at),
        Question(id="pending-first",owner_id=teacher.id,import_job_id=first_job.id,kind=QuestionKind.SINGLE,stem_markdown="First pending",options=[{"markdown":"4"},{"markdown":"5"}],answer={"option":0}),
        Question(id="approved-second",owner_id=teacher.id,import_job_id=second_job.id,kind=QuestionKind.SINGLE,stem_markdown="Second approved",options=[{"markdown":"4"},{"markdown":"5"}],answer={"option":0},approved_at=approved_at),
    ]);db.commit()

    result=questions(page=1,page_size=20,approved=True,import_job_id=first_job.id,user=teacher,db=db)

    assert result["total"]==1
    assert [item["id"] for item in result["items"]]==["approved-first"]
    assert result["items"][0]["import_job_id"]==first_job.id

def test_teacher_can_create_and_publish_a_test_from_one_approved_pdf():
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    source=SourceDocument(id="source",owner_id=teacher.id,filename="worksheet-1.pdf",object_key="documents/teacher/worksheet-1.pdf",sha256="a" * 64,media_type="application/pdf",size_bytes=10)
    job=ImportJob(id="job",owner_id=teacher.id,question_document_id=source.id,status=ImportStatus.APPROVED)
    approved_at=datetime.now(timezone.utc)
    questions_from_source=[
        Question(id="q1",owner_id=teacher.id,import_job_id=job.id,source_number="1",kind=QuestionKind.SINGLE,stem_markdown="One",options=[{"markdown":"4"},{"markdown":"5"}],answer={"option":0},approved_at=approved_at),
        Question(id="q10",owner_id=teacher.id,import_job_id=job.id,source_number="10",kind=QuestionKind.SINGLE,stem_markdown="Ten",options=[{"markdown":"4"},{"markdown":"5"}],answer={"option":0},approved_at=approved_at),
        Question(id="q2",owner_id=teacher.id,import_job_id=job.id,source_number="2",kind=QuestionKind.SINGLE,stem_markdown="Two",options=[{"markdown":"4"},{"markdown":"5"}],answer={"option":1},approved_at=approved_at),
        Question(id="q3",owner_id=teacher.id,import_job_id=job.id,source_number="3",kind=QuestionKind.SUBJECTIVE,stem_markdown="Explain",options=[],answer={"value":"Because"},solution_markdown="The forces balance at equilibrium.",approved_at=approved_at),
    ]
    db.add_all([teacher,source,job,*questions_from_source]);db.commit()
    data=ImportTestCreate(title="Worksheet 1",subject="Physics",duration_minutes=30,max_attempts=1,marks_per_question=4,delivery_blueprint={"preset":"custom","instructions":"Use the supplied worksheet.","sections":[{"id":"worksheet","title":"Worksheet 1"}]})

    result=create_import_test(job.id,data,teacher,db)

    saved=db.get(Assessment,result["id"])
    assert result["status"]=="published"
    assert result["question_count"]==4
    assert saved.published_version_id is not None
    version=db.get(AssessmentVersion,saved.published_version_id)
    assert [item["id"] for item in version.content["questions"]]==["q1","q2","q3","q10"]
    assert version.content["delivery_blueprint"]["instructions"]=="Use the supplied worksheet."
    assert version.content["delivery_blueprint"]["sections"][0]["title"]=="Worksheet 1"

def test_admin_can_publish_a_teacher_owned_approved_pdf():
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    admin=User(id="admin",name="Admin",email="admin@example.com",password_hash="x",role=Role.ADMIN)
    source=SourceDocument(id="source",owner_id=teacher.id,filename="worksheet.pdf",object_key="documents/teacher/worksheet.pdf",sha256="a" * 64,media_type="application/pdf",size_bytes=10)
    job=ImportJob(id="job",owner_id=teacher.id,question_document_id=source.id,status=ImportStatus.APPROVED)
    question=Question(id="q1",owner_id=teacher.id,import_job_id=job.id,source_number="1",kind=QuestionKind.SINGLE,stem_markdown="One",options=[{"markdown":"4"},{"markdown":"5"}],answer={"option":0},approved_at=datetime.now(timezone.utc))
    db.add_all([teacher,admin,source,job,question]);db.commit()

    page=questions(page=1,page_size=20,approved=True,import_job_id=job.id,user=admin,db=db)
    result=create_import_test(job.id,ImportTestCreate(title="Worksheet",subject="Physics",duration_minutes=30),admin,db)

    assert [item["id"] for item in page["items"]]==[question.id]
    assert result["status"]=="published"

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
    completed=submit(attempt.id,SubmitIn(revision=0),student,db)
    assert completed["submitted_at"] is not None
    assert completed["score"]==4
    assert db.get(Attempt,attempt.id).submitted_at is not None

def test_integrity_violation_cancels_once_and_is_visible_in_teacher_analytics():
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    student=User(id="student",name="Student",email="student@example.com",password_hash="x",role=Role.STUDENT,status=AccountStatus.ACTIVE)
    classroom=Classroom(id="class",owner_id=teacher.id,name="12A")
    test=Assessment(id="test",owner_id=teacher.id,title="Test",subject="Subject",status=AssessmentStatus.PUBLISHED,duration_seconds=3600,max_attempts=1)
    version=AssessmentVersion(id="version",test_id=test.id,ordinal=1,content={"questions":[{"id":"question","kind":"single_choice","answer":{"option":0},"scoring":{"correct":4,"incorrect":-1,"unanswered":0}}]},published_at=datetime.now(timezone.utc))
    test.published_version_id=version.id
    prior=Attempt(id="prior-attempt",test_id=test.id,version_id=version.id,student_id=student.id,started_at=datetime(2025,1,1,tzinfo=timezone.utc),ends_at=datetime(2025,1,1,1,tzinfo=timezone.utc),submitted_at=datetime(2025,1,1,0,30,tzinfo=timezone.utc),score=4,cohort_classes=[{"id":"class","name":"12A"}])
    attempt=Attempt(id="attempt",test_id=test.id,version_id=version.id,student_id=student.id,started_at=datetime.now(timezone.utc),ends_at=datetime.now(timezone.utc).replace(year=2030),cohort_classes=[{"id":"class","name":"12A"}])
    response=AnswerResponse(attempt_id=attempt.id,question_id="question",answer={"option":0})
    db.add_all([teacher,student,classroom,test,version,prior,attempt,response,ClassMembership(class_id=classroom.id,student_id=student.id),ClassAssignment(test_id=test.id,class_id=classroom.id)]);db.commit()

    first=integrity_violation(attempt.id,IntegrityViolationIn(event_type="tab_hidden"),student,db)
    second=integrity_violation(attempt.id,IntegrityViolationIn(event_type="window_blur"),student,db)

    assert first["integrity_flagged"] is True
    assert first["cancellation_reason"]=="tab_hidden"
    assert first["score"]==4
    assert second["cancellation_reason"]=="tab_hidden"
    assert db.query(AttemptEvent).filter_by(attempt_id=attempt.id,event_type="integrity_violation").count()==1
    db.query(ClassMembership).delete();db.query(ClassAssignment).delete();db.commit()
    analytics=assessment_analytics(test.id,teacher,db)
    assert analytics["summary"]["cancelled"]==1
    assert analytics["summary"]["flagged"]==1
    assert analytics["summary"]["submitted"]==1
    assert analytics["summary"]["currently_assigned"]==0
    assert analytics["batches"][0]["flagged"]==1
    row=analytics["students"][0]
    assert row["status"]=="cancelled"
    assert row["integrity_flagged"] is True
    assert row["attempts"][0]["incidents"][0]["event_type"]=="tab_hidden"

def test_integrity_report_after_deadline_flags_without_replacing_expiry_submission():
    db=make_db()
    student=User(id="student",name="Student",email="student@example.com",password_hash="x",role=Role.STUDENT,status=AccountStatus.ACTIVE)
    test=Assessment(id="test",owner_id="teacher",title="Test",subject="Subject",status=AssessmentStatus.PUBLISHED,duration_seconds=60,max_attempts=1)
    version=AssessmentVersion(id="version",test_id=test.id,ordinal=1,content={"questions":[]},published_at=datetime.now(timezone.utc))
    test.published_version_id=version.id
    deadline=datetime(2025,1,1,tzinfo=timezone.utc)
    attempt=Attempt(id="attempt",test_id=test.id,version_id=version.id,student_id=student.id,started_at=deadline.replace(year=2024),ends_at=deadline)
    db.add_all([student,test,version,attempt]);db.commit()

    result=integrity_violation(attempt.id,IntegrityViolationIn(event_type="page_hidden",client_time=deadline),student,db)

    assert result["submitted_at"]==deadline
    assert result["cancelled_at"] is None
    assert result["integrity_flagged"] is True
    assert db.query(AttemptEvent).filter_by(attempt_id=attempt.id,event_type="integrity_violation").count()==1

def test_batch_analytics_use_the_batch_snapshot_for_each_attempt():
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    student=User(id="student",name="Student",email="student@example.com",password_hash="x",role=Role.STUDENT)
    first_class=Classroom(id="first",owner_id=teacher.id,name="Batch A")
    second_class=Classroom(id="second",owner_id=teacher.id,name="Batch B")
    test=Assessment(id="test",owner_id=teacher.id,title="Test",subject="Subject",status=AssessmentStatus.PUBLISHED,duration_seconds=60,max_attempts=2)
    version=AssessmentVersion(id="version",test_id=test.id,ordinal=1,content={"questions":[]},published_at=datetime.now(timezone.utc));test.published_version_id=version.id
    first=Attempt(id="first-attempt",test_id=test.id,version_id=version.id,student_id=student.id,started_at=datetime(2025,1,1,tzinfo=timezone.utc),ends_at=datetime(2025,1,1,1,tzinfo=timezone.utc),submitted_at=datetime(2025,1,1,0,30,tzinfo=timezone.utc),score=4,cohort_classes=[{"id":"first","name":"Batch A"}])
    second=Attempt(id="second-attempt",test_id=test.id,version_id=version.id,student_id=student.id,started_at=datetime(2025,2,1,tzinfo=timezone.utc),ends_at=datetime(2025,2,1,1,tzinfo=timezone.utc),submitted_at=datetime(2025,2,1,0,10,tzinfo=timezone.utc),cancelled_at=datetime(2025,2,1,0,10,tzinfo=timezone.utc),cancellation_reason="window_blur",integrity_flagged=True,score=0,cohort_classes=[{"id":"second","name":"Batch B"}])
    db.add_all([teacher,student,first_class,second_class,test,version,first,second,ClassAssignment(test_id=test.id,class_id=first_class.id),ClassAssignment(test_id=test.id,class_id=second_class.id)]);db.commit()

    analytics=assessment_analytics(test.id,teacher,db)
    batches={row["classroom"]["name"]:row for row in analytics["batches"]}

    assert batches["Batch A"]["submitted"]==1
    assert batches["Batch A"]["flagged"]==0
    assert batches["Batch B"]["submitted"]==0
    assert batches["Batch B"]["cancelled"]==1
    assert batches["Batch B"]["flagged"]==1

def test_expiry_worker_finalizes_attempt_without_a_browser(monkeypatch):
    db=make_db()
    student=User(id="student",name="Student",email="student@example.com",password_hash="x",role=Role.STUDENT,status=AccountStatus.ACTIVE)
    test=Assessment(id="test",owner_id="teacher",title="Test",subject="Subject",status=AssessmentStatus.PUBLISHED,duration_seconds=60,max_attempts=1)
    version=AssessmentVersion(id="version",test_id=test.id,ordinal=1,content={"questions":[]},published_at=datetime.now(timezone.utc))
    attempt=Attempt(id="attempt",test_id=test.id,version_id=version.id,student_id=student.id,started_at=datetime.now(timezone.utc),ends_at=datetime.now(timezone.utc).replace(year=2020))
    db.add_all([student,test,version,attempt]);db.commit()
    attempt_id=attempt.id  # The worker owns/closes its session; retain the stable fixture ID.
    monkeypatch.setattr("app.tasks.SessionLocal",lambda:db)

    assert finalize_expired_attempts.run()=={"finalized":1}
    assert db.get(Attempt,attempt_id).submitted_at is not None

def test_existing_attempt_is_not_stranded_after_assignment_changes():
    db=make_db()
    student=User(id="student",name="Student",email="student@example.com",password_hash="x",role=Role.STUDENT,status=AccountStatus.ACTIVE)
    test=Assessment(id="test",owner_id="teacher",title="Test",subject="Subject",status=AssessmentStatus.PUBLISHED,duration_seconds=60,max_attempts=1)
    version=AssessmentVersion(id="version",test_id="test",ordinal=1,content={"questions":[]},published_at=datetime.now(timezone.utc))
    attempt=Attempt(id="attempt",test_id="test",version_id="version",student_id="student",started_at=datetime.now(timezone.utc),ends_at=datetime.now(timezone.utc))
    db.add_all([student,test,version,attempt]);db.commit()
    assert get_attempt(attempt.id,student,db)["id"]==attempt.id
