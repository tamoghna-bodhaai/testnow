"""Execution context shared by worker sessions and asset writers.

Ownership loss must bypass task handlers which catch Exception and write a
failure state: a fenced process has no authority to make even that write.
"""
from contextvars import ContextVar
from dataclasses import dataclass

from sqlalchemy import text


class OwnershipLost(BaseException):
    pass


@dataclass
class Execution:
    connection: object
    raw_connection: object
    job_id: str
    generation: int

    def check(self, connection=None, *, lock=False):
        connection = connection if connection is not None else self.connection
        if (connection is not self.connection or connection.closed or connection.invalidated
                or connection.connection.dbapi_connection is not self.raw_connection):
            raise OwnershipLost("Worker database session was lost")
        suffix = " FOR UPDATE" if lock else ""
        started = not connection.in_transaction()
        row = connection.execute(text(
            "SELECT generation, status FROM durable_jobs WHERE id=:id" + suffix
        ), {"id": self.job_id}).first()
        if started:
            connection.commit()
        if not row or row.generation != self.generation or row.status != "running":
            raise OwnershipLost("Worker generation was fenced")


current_execution: ContextVar[Execution | None] = ContextVar("job_execution", default=None)


def asset_key(key: str) -> str:
    """Keep a disconnected process's uploads separate from its replacement."""
    owner = current_execution.get()
    if owner is None:
        return key
    owner.check()
    # Keep the original prefix so existing source-specific cleanup still works.
    path, dot, extension = key.rpartition(".")
    suffix = f"-job-{owner.job_id}-{owner.generation}"
    return f"{path}{suffix}.{extension}" if dot else key + suffix
