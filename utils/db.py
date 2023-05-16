import datetime
import sys
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


__all__ = [
    "registry",
    "get_or_none",
    "VerifyCode",
    "Student",
    "BannedStudentID",
    "Assignments",
    "Tutors",
    "UptimeEntry",
    "JimmyBans",
]

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)

if Path("/data").exists():
    _pth = "/data/main.db"
    try:
        Path(_pth).touch()
    except PermissionError as e:
        print("Failed to create database:", e, file=sys.stderr)
        sys.exit(1)
else:
    _pth = "/main.db"

registry = orm.ModelRegistry(Database("sqlite://" + _pth))


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
        "name": orm.String(min_length=2, max_length=32),
    }
    if TYPE_CHECKING:
        id: int
        code: str
        bind: int
        student_id: str
        name: str


class Student(orm.Model):
    registry = registry
    tablename = "students"
    fields = {
        "entry_id": orm.UUID(primary_key=True, default=uuid.uuid4),
        "id": orm.String(min_length=7, max_length=7, unique=True),
        "user_id": orm.BigInteger(unique=True),
        "name": orm.String(min_length=2, max_length=32),
        "access_token": orm.String(min_length=6, max_length=128, default=None, allow_null=True),
        "ip_info": orm.JSON(default=None, allow_null=True),
        "access_token_hash": orm.String(min_length=128, max_length=128, default=None, allow_null=True), 
    }
    if TYPE_CHECKING:
        entry_id: uuid.UUID
        id: str
        user_id: int
        name: str
        access_token: str | None
        ip_info: dict | None
        access_token_hash: str | None


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
        "assignees": orm.JSON(default=[]),
        # "description": orm.Text(min_length=2, max_length=2000, allow_null=True, default=None),
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
        assignees: list[int]
        # description: Optional[str]


class StarBoardMessage(orm.Model):
    tablename = "starboard"
    registry = registry
    fields = {
        "entry_id": orm.UUID(primary_key=True, default=uuid.uuid4),
        "id": orm.BigInteger(unique=True),
        "channel": orm.BigInteger(),
        "starboard_message": orm.BigInteger(default=None, allow_null=True),
    }

    if TYPE_CHECKING:
        entry_id: uuid.UUID
        id: int
        channel: int
        starboard_message: int | None


class UptimeEntry(orm.Model):
    tablename = "uptime"
    registry = registry
    fields = {
        "entry_id": orm.UUID(primary_key=True, default=uuid.uuid4),
        "target_id": orm.String(min_length=2, max_length=128),
        "target": orm.String(min_length=2, max_length=128),
        "is_up": orm.Boolean(),
        "timestamp": orm.Float(default=lambda: datetime.datetime.utcnow().timestamp()),
        "response_time": orm.Integer(allow_null=True),
        "notes": orm.Text(allow_null=True, default=None),
    }

    if TYPE_CHECKING:
        entry_id: uuid.UUID
        target_id: str
        target: str
        is_up: bool
        timestamp: float
        response_time: int | None
        notes: str | None


class JimmyBans(orm.Model):
    tablename = "jimmy_bans"
    registry = registry
    fields = {
        "entry_id": orm.UUID(primary_key=True, default=uuid.uuid4),
        "user_id": orm.BigInteger(),
        "reason": orm.Text(allow_null=True, default=None),
        "timestamp": orm.Float(default=lambda: datetime.datetime.utcnow().timestamp()),
        "until": orm.Float(),
    }
    if TYPE_CHECKING:
        entry_id: uuid.UUID
        user_id: int
        reason: str | None
        timestamp: float
        until: float | None


class AccessTokens(db.Model):
    tablename = "access_tokens"
    registry = registry
    fields = {
        "entry_id": orm.UUID(primary_key=True, default=uuid.uuid4),
        "user_id": orm.BigInteger(unique=True),
        "access_token": orm.String(min_length=6, max_length=128),
        "ip_info": orm.JSON(default=None, allow_null=True),
    }

    if TYPE_CHECKING:
        entry_id: uuid.UUID
        user_id: int
        access_token: str
        ip_info: dict | None
