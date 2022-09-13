import uuid
from typing import TYPE_CHECKING

import orm
from databases import Database
import os
from pathlib import Path

os.chdir(Path(__file__).parent)


registry = orm.ModelRegistry(Database("sqlite:///main.db"))


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
