from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from .config import settings
from .job_context import current_execution

_url=settings().database_url
_engine_options={"pool_pre_ping":True}
if not _url.startswith("sqlite"):_engine_options.update({"pool_size":10,"max_overflow":20})
engine = create_engine(_url, **_engine_options)
class WorkerSession(Session):
    def __init__(self, *args, **kwargs):
        owner = current_execution.get()
        if owner is not None:
            kwargs["bind"] = owner.connection
        super().__init__(*args, **kwargs)
        self.info["job_owner"] = owner


@event.listens_for(WorkerSession, "after_begin")
def check_worker_transaction(session, transaction, connection):
    owner = session.info.get("job_owner")
    if owner is not None:
        owner.check(connection)


@event.listens_for(WorkerSession, "before_commit")
def fence_worker_commit(session):
    owner = session.info.get("job_owner")
    if owner is not None:
        # Lock and validate the ledger row in the same transaction as business
        # writes. A stale process cannot commit even its exception handler.
        owner.check(session.connection(), lock=True)


SessionLocal = sessionmaker(bind=engine, class_=WorkerSession, autoflush=False, autocommit=False)
class Base(DeclarativeBase): pass
def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()
