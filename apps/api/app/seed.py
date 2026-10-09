"""Explicitly opt-in local demonstration data. Never enable this in production."""
from sqlalchemy.orm import Session
from .models import Classroom, ClassMembership, Role, User
from .security import hash_password, uid

DEMO_PASSWORD="TestNowDev!2026"
DEMO_TEACHER="teacher@testnow.local"
DEMO_STUDENTS=("student1@testnow.local", "student2@testnow.local")

def ensure_demo_data(db:Session):
    people=[("Demo Teacher", DEMO_TEACHER, Role.TEACHER), ("Student One", DEMO_STUDENTS[0], Role.STUDENT), ("Student Two", DEMO_STUDENTS[1], Role.STUDENT)]
    users={}
    for name,email,role in people:
        user=db.query(User).filter_by(email=email).first()
        if not user:
            user=User(id=uid(),name=name,email=email,password_hash=hash_password(DEMO_PASSWORD),role=role,is_verified=True)
            db.add(user)
        users[email]=user
    db.flush()
    teacher=users[DEMO_TEACHER]
    classroom=db.query(Classroom).filter_by(owner_id=teacher.id,name="Demo Physics").first()
    if not classroom:
        classroom=Classroom(id=uid(),owner_id=teacher.id,name="Demo Physics",description="Local development roster")
        db.add(classroom)
        db.flush()
    for email in DEMO_STUDENTS:
        if not db.get(ClassMembership,{"class_id":classroom.id,"student_id":users[email].id}):
            db.add(ClassMembership(class_id=classroom.id,student_id=users[email].id))
    db.commit()
