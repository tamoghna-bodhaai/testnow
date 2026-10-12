from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.main import set_access_code, teacher_view
from app.models import AccountStatus, Role, User
from app.schemas import LoginIn, TeacherCreate
from app.security import verify_password

def test_access_code_models_require_exactly_five_digits():
    assert LoginIn(email="a@example.com",access_code="12345",role="student").access_code=="12345"
    assert TeacherCreate(name="Teacher",email="teacher@example.com",access_code="54321").access_code=="54321"

def test_issuing_access_code_activates_account_without_exposing_hash():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine);db=sessionmaker(bind=engine)()
    teacher=User(id="teacher",name="Teacher",email="teacher@example.com",password_hash="",role=Role.TEACHER,status=AccountStatus.PENDING_CODE)
    db.add(teacher);db.flush();set_access_code(teacher,"12345",db);db.commit()
    assert teacher.status==AccountStatus.ACTIVE
    assert verify_password("12345",teacher.access_code_hash)
    assert "access_code_hash" not in teacher_view(teacher,db)
