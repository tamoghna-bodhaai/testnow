from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import main
from app.main import assigned_student_ids, question_media, student_can_access, test_analytics as assessment_analytics
from app.models import Attempt, Classroom, ClassMembership, Question, QuestionKind, Role, Test as Assessment, TestClassAssignment as ClassAssignment, TestStatus as AssessmentStatus, TestVersion as AssessmentVersion, User

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

def test_question_media_streams_bytes_without_object_store_redirect(monkeypatch):
    db=make_db()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="x",role=Role.TEACHER)
    question=Question(id="question",owner_id="teacher",kind=QuestionKind.SINGLE,stem_markdown="x",options=[{"value":"a","markdown":"a"}],diagrams=[{"object_key":"diagram.png","alt":"Diagram"}])
    db.add_all([teacher,question]);db.commit()
    monkeypatch.setattr(main,"get",lambda key:b"image-bytes")
    response=question_media("question",0,teacher,db)
    assert response.media_type=="image/png"
    assert response.headers["cache-control"]=="private, max-age=300"
