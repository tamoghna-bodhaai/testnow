"""Read-only support context scoped to one teacher and existing ownership rules."""
from fastapi import APIRouter,Depends,HTTPException,Query
from sqlalchemy import exists
from sqlalchemy.orm import Session
from .db import get_db
from .models import Attempt,Classroom,ClassMembership,ImportJob,Question,Response,Role,TaxonomyNode,Test,TestClassAssignment,TestVersion,User
from .security import require
from .services import audit
router=APIRouter()


def teacher_record(db,teacher_id):
    teacher=db.query(User).filter_by(id=teacher_id,role=Role.TEACHER).first()
    if not teacher:raise HTTPException(404,'Teacher not found')
    return teacher


def public_user(user):
    return {"id":user.id,"name":user.name,"email":user.email,"role":user.role.value,"status":user.status.value,"last_login_at":user.last_login_at}


def support_query(db,teacher_id,kind):
    if kind=='class':return db.query(Classroom).filter_by(owner_id=teacher_id)
    if kind=='student':return db.query(User).filter(User.role==Role.STUDENT,exists().where(ClassMembership.student_id==User.id,ClassMembership.class_id==Classroom.id,Classroom.owner_id==teacher_id))
    if kind=='test':return db.query(Test).filter_by(owner_id=teacher_id)
    if kind=='attempt':return db.query(Attempt).join(Test,Test.id==Attempt.test_id).filter(Test.owner_id==teacher_id)
    raise HTTPException(422,'Choose class, student, test or attempt support context')


def support_item(db,kind,row):
    if kind=='class':return {"id":row.id,"label":row.name,"student_count":db.query(ClassMembership).filter_by(class_id=row.id).count()}
    if kind=='student':return {"id":row.id,"label":row.name,"email":row.email,"status":row.status.value}
    if kind=='test':return {"id":row.id,"label":row.title,"subject":row.subject,"status":row.status.value}
    student=db.get(User,row.student_id);test=db.get(Test,row.test_id)
    return {"id":row.id,"label":f'{student.name if student else row.student_id} · {test.title}',"test_id":row.test_id,"student_id":row.student_id,"submitted_at":row.submitted_at,"score":row.score,"integrity_flagged":row.integrity_flagged}


@router.get('/admin/teachers/{teacher_id}')
def teacher_support(teacher_id:str,user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    teacher=teacher_record(db,teacher_id)
    return {"teacher":public_user(teacher),"counts":{kind:support_query(db,teacher_id,kind).count() for kind in ('class','student','test','attempt')}}


@router.get('/admin/teachers/{teacher_id}/resources')
def teacher_resources(teacher_id:str,kind:str='class',page:int=Query(default=1,ge=1),page_size:int=Query(default=20,ge=1,le=100),user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    teacher_record(db,teacher_id);query=support_query(db,teacher_id,kind)
    model={'class':Classroom,'student':User,'test':Test,'attempt':Attempt}[kind]
    total=query.count();rows=query.order_by(model.created_at.desc(),model.id).offset((page-1)*page_size).limit(page_size).all()
    return {"items":[support_item(db,kind,row) for row in rows],"total":total,"page":page,"page_size":page_size,"has_more":page*page_size<total}


@router.get('/admin/teachers/{teacher_id}/resources/{kind}/{resource_id}')
def teacher_resource(teacher_id:str,kind:str,resource_id:str,page:int=Query(default=1,ge=1),page_size:int=Query(default=25,ge=1,le=100),user:User=Depends(require(Role.ADMIN)),db:Session=Depends(get_db)):
    teacher_record(db,teacher_id);query=support_query(db,teacher_id,kind)
    model={'class':Classroom,'student':User,'test':Test,'attempt':Attempt}[kind]
    row=query.filter(model.id==resource_id).first()
    if not row:raise HTTPException(404,'This resource is not in the selected teacher support scope')
    result={"kind":kind,**support_item(db,kind,row)}
    if kind=='class':
        students=db.query(User).join(ClassMembership,ClassMembership.student_id==User.id).filter(ClassMembership.class_id==row.id).order_by(User.name,User.id)
        result.update({"students":[public_user(student) for student in students.offset((page-1)*page_size).limit(page_size)],"students_total":students.count(),"page":page,"has_more":page*page_size<students.count()})
    elif kind=='student':
        result['batches']=[{"id":batch.id,"name":batch.name} for batch in db.query(Classroom).join(ClassMembership,ClassMembership.class_id==Classroom.id).filter(Classroom.owner_id==teacher_id,ClassMembership.student_id==row.id)]
    elif kind=='test':
        version=db.get(TestVersion,row.published_version_id) if row.published_version_id else db.query(TestVersion).filter_by(test_id=row.id).order_by(TestVersion.ordinal.desc()).first()
        questions=(version.content or {}).get('questions',[]) if version else []
        result.update({"version_id":version.id if version else None,"duration_seconds":row.duration_seconds,"questions":questions[(page-1)*page_size:page*page_size],"questions_total":len(questions),"page":page,"has_more":page*page_size<len(questions)})
    else:
        version=db.get(TestVersion,row.version_id);questions={question.get('id'):question for question in (version.content or {}).get('questions',[]) if isinstance(question,dict) and question.get('id')} if version else {}
        responses=db.query(Response).filter_by(attempt_id=row.id).order_by(Response.question_id)
        result.update({"version_id":row.version_id,"started_at":row.started_at,"ends_at":row.ends_at,"cancellation_reason":row.cancellation_reason,
                       "responses":[{"question_id":response.question_id,"saved_answer":response.answer,"expected_answer":questions.get(response.question_id,{}).get('answer'),"stem_markdown":questions.get(response.question_id,{}).get('stem_markdown')} for response in responses.offset((page-1)*page_size).limit(page_size)],
                       "responses_total":responses.count(),"page":page,"has_more":page*page_size<responses.count()})
    audit(db,user.id,'admin.support_viewed',kind,resource_id,{"teacher_id":teacher_id,"page":page});db.commit()
    return result
