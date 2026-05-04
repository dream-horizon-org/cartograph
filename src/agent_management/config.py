"""DB and API configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    openai_api_key: str
    embedding_model: str


def get_config() -> Config:
    return Config(
        db_host=os.environ.get("DB_HOST", "localhost"),
        db_port=int(os.environ.get("DB_PORT", "5432")),
        db_name=os.environ.get("DB_NAME", "cartograph"),
        db_user=os.environ.get("DB_USER", "cartograph"),
        db_password=os.environ.get("DB_PASSWORD", "cartograph"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
        embedding_model=os.environ.get(
            "EMBEDDING_MODEL", "text-embedding-3-small"
        ),
    )
