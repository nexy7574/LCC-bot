import datetime
import uuid
from typing import TYPE_CHECKING, Optional, TypeVar
from enum import IntEnum, auto

import orm
from databases import Database
import os
from pathlib import Path


class Tutors(IntEnum):
    JAY = auto()
    ZACH = auto()
    IAN = auto()
    REBECCA = auto()
    LUPUPA = auto()
    OTHER = -1  # not auto() because if we add more it could mess things up.


os.chdir(Path(__file__).parent.parent)


__all__ = ["registry", "get_or_none", "VerifyCode", "Student", "BannedStudentID", "Assignments", "Tutors"]

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


registry = orm.ModelRegistry(Database("sqlite:///main.db"))


async def get_or_none(model: T, **kw) -> Optional[T_co]:
    """Returns none or the required thing."""
    try:
        return await model.objects.get(**kw)
    except orm.NoMatch:
        return


class VerifyCode(orm.Model):
    registry = registry
    tablename = "codes"
    fields = {
        "id": orm.Integer(primary_key=True),
        "code": orm.String(min_length=8, max_length=64, unique=True),
        "bind": orm.BigInteger(),
        "student_id": orm.String(min_length=7, max_length=7),
    }
    if TYPE_CHECKING:
        id: int
        code: str
        bind: int
        student_id: str


class Student(orm.Model):
    registry = registry
    tablename = "students"
    fields = {
        "entry_id": orm.UUID(primary_key=True, default=uuid.uuid4),
        "id": orm.String(min_length=7, max_length=7, unique=True),
        "user_id": orm.BigInteger(unique=True),
    }
    if TYPE_CHECKING:
        entry_id: uuid.UUID
        id: str
        user_id: int


class BannedStudentID(orm.Model):
    registry = registry
    tablename = "banned"
    fields = {
        "entry_id": orm.UUID(primary_key=True, default=uuid.uuid4),
        "student_id": orm.String(min_length=7, max_length=7, unique=True),
        "associated_account": orm.BigInteger(default=None),
        "banned_at_timestamp": orm.Float(default=lambda: datetime.datetime.utcnow().timestamp()),
    }
    if TYPE_CHECKING:
        entry_id: uuid.UUID
        student_id: str
        associated_account: Optional[int]
        banned_at_timestamp: float


class Assignments(orm.Model):
    registry = registry
    fields = {
        "entry_id": orm.Integer(primary_key=True, default=None),
        "created_by": orm.ForeignKey(Student, allow_null=True),
        "title": orm.String(min_length=2, max_length=2000),
        "classroom": orm.URL(allow_null=True, default=None, max_length=4096),
        "shared_doc": orm.URL(allow_null=True, default=None, max_length=4096),
        "created_at": orm.Float(default=lambda: datetime.datetime.now().timestamp()),
        "due_by": orm.Float(),
        "tutor": orm.Enum(Tutors),
        "reminders": orm.JSON(default=[]),
        "finished": orm.Boolean(default=False),
        "submitted": orm.Boolean(default=False),
    }
    if TYPE_CHECKING:
        entry_id: int
        created_by: Student
        title: str
        classroom: Optional[str]
        shared_doc: Optional[str]
        created_at: float
        due_by: float
        tutor: Tutors
        reminders: list[str]
        finished: bool
        submitted: bool
