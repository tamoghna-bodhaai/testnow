import hashlib, secrets, uuid
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, Request, Response, status
from argon2 import PasswordHasher
from sqlalchemy.orm import Session
from .db import get_db
from .config import settings
from .models import Role, Session as UserSession, User

hasher=PasswordHasher(); COOKIE="testnow_session"; SESSION_DAYS=14
def hash_password(value:str)->str:return hasher.hash(value)
def verify_password(value:str, hashed:str)->bool:
    try:return hasher.verify(hashed,value)
    except Exception:return False
def new_session(response:Response, db:Session, user:User):
    raw=secrets.token_urlsafe(48); sid=hashlib.sha256(raw.encode()).hexdigest(); expires=datetime.now(timezone.utc)+timedelta(days=SESSION_DAYS)
    db.add(UserSession(id=sid,user_id=user.id,expires_at=expires)); db.commit()
    response.set_cookie(COOKIE,raw,httponly=True,secure=settings().cookie_secure,samesite="lax",max_age=SESSION_DAYS*86400,path="/")
def current_user(request:Request, db:Session=Depends(get_db))->User:
    raw=request.cookies.get(COOKIE)
    if not raw:raise HTTPException(status_code=401,detail="Authentication required")
    sid=hashlib.sha256(raw.encode()).hexdigest(); row=db.get(UserSession,sid)
    if not row or row.revoked_at or row.expires_at<datetime.now(timezone.utc):raise HTTPException(status_code=401,detail="Session expired")
    user=db.get(User,row.user_id)
    if not user:raise HTTPException(status_code=401,detail="Authentication required")
    return user
def require(*roles:Role):
    def inner(user:User=Depends(current_user)):
        if roles and user.role not in roles:raise HTTPException(status_code=403,detail="Insufficient permissions")
        return user
    return inner
def uid()->str:return str(uuid.uuid4())
