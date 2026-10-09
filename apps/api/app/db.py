from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings

_url=settings().database_url
_engine_options={"pool_pre_ping":True}
if not _url.startswith("sqlite"):_engine_options.update({"pool_size":10,"max_overflow":20})
engine = create_engine(_url, **_engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
class Base(DeclarativeBase): pass
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()
