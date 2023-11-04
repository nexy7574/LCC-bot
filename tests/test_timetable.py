import json
import warnings
from pathlib import Path
from typing import Union

import pytest

from utils import Tutors

file = (Path(__file__).parent.parent / "utils" / "timetable.json").resolve()


def is_sane_time(time: list[int, int]) -> Union[bool, AssertionError]:
    def inner():
        hour, minute = time
        assert hour >= 9, "We aren't in college before 9am"
        assert hour <= 5, "We aren't in college after 5pm"
        assert minute in range(0, 60), "Invalid minute range - must be between (inclusive) 0 & 59"
        if minute % 15 != 0:
            warnings.warn(
                UserWarning("Time '%s:%s' is probably not a valid timetable time, as lessons are every 15 minutes.")
            )
        return True

    try:
        return inner()
    except AssertionError as e:
        return e


def get_lesson_times() -> list[list[int, int]]:
    data = json.loads(file.read_text())
    master = []
    for day, lessons in data.items():
        for lesson in lessons:
            master.append(lesson["start"])
            master.append(lesson["end"])
    return master


@pytest.mark.dependency()
def test_exists():
    assert file.exists()
    assert file.is_file()


@pytest.mark.dependency(depends=["test_exists"])
def test_can_read():
    try:
        file.read_text()
    except (IOError, OSError):
        assert 0, "Unable to read file."


@pytest.mark.dependency(depends=["test_exists", "test_can_read"])
def test_valid_json():
    try:
        data = json.loads(file.read_text())
    except (IOError, OSError, json.JSONDecodeError):
        data = None
    assert data is not None


@pytest.mark.dependency(depends=["test_exists", "test_can_read", "test_valid_json"])
def test_valid_structure():
    data = json.loads(file.read_text())
    assert isinstance(data, dict)
    assert len(data) == 5, "insufficient days"
    for key, value in data.items():
        assert isinstance(key, str)
        assert key.islower()
        assert isinstance(value, list)
        for entry in value:
            assert isinstance(entry, dict)
            required_keys = {"name": 0, "start": 0, "end": 0, "tutor": 0, "room": 0}
            for entry_key, entry_value in entry.items():
                assert isinstance(entry_key, str)
                assert entry_key in required_keys, f"unknown key {entry_key!r}"
                required_keys[entry_key] = 1
                if isinstance(entry_value, list):
                    assert len(entry_value) == 2
                    assert [isinstance(x, int) for x in entry_value]
                else:
                    assert isinstance(entry_value, str)

            assert all(required_keys[k] for k in required_keys.keys())


@pytest.mark.dependency(depends=["test_exists", "test_can_read", "test_valid_json", "test_valid_structure"])
@pytest.mark.parametrize("time", get_lesson_times())
def test_sane_times(time: list[int, int]):
    assert is_sane_time(time), "insane lesson time: %s:%s" % time


@pytest.mark.dependency(depends=["test_exists", "test_can_read", "test_valid_json"])
def test_sane_tutors():
    data = json.loads(file.read_text())
    for day, lessons in data.items():
        for lesson in lessons:
            exists = getattr(Tutors, lesson["tutor"].upper(), None) is not None
            assert exists, "Tutor %s is invalid" % lesson["tutor"]
