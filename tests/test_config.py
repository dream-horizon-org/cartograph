import os
import pytest
from agent_management.config import get_config


def test_get_config_defaults(monkeypatch):
    for key in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
                "OPENAI_API_KEY", "EMBEDDING_MODEL"):
        monkeypatch.delenv(key, raising=False)

    cfg = get_config()
    assert cfg.db_host == "localhost"
    assert cfg.db_port == 5432
    assert cfg.db_name == "cartograph"
    assert cfg.db_user == "cartograph"
    assert cfg.db_password == "cartograph"
    assert cfg.embedding_model == "text-embedding-3-small"


def test_get_config_from_env(monkeypatch):
    monkeypatch.setenv("DB_HOST", "myhost")
    monkeypatch.setenv("DB_PORT", "5433")
    monkeypatch.setenv("DB_NAME", "mydb")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    cfg = get_config()
    assert cfg.db_host == "myhost"
    assert cfg.db_port == 5433
    assert cfg.db_name == "mydb"
    assert cfg.openai_api_key == "sk-test"
