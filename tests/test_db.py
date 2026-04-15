import os
import sqlite3
import tempfile

import pytest

from cartograph.db import init_db


@pytest.fixture
def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.unlink(path)


def test_init_db_creates_agent_runs_table(db_path):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_runs'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_init_db_creates_trigger_queue_table(db_path):
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trigger_queue'"
    )
    assert cursor.fetchone() is not None
    conn.close()


def test_init_db_is_idempotent(db_path):
    init_db(db_path)
    init_db(db_path)  # should not raise
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    )
    count = cursor.fetchone()[0]
    assert count == 2
    conn.close()
