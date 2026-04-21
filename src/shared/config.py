"""Shared configuration — reads from environment variables."""

import os


DB_HOST = os.getenv("CARTOGRAPH_DB_HOST", "localhost")
DB_PORT = int(os.getenv("CARTOGRAPH_DB_PORT", "5432"))
DB_NAME = os.getenv("CARTOGRAPH_DB_NAME", "cartograph")
DB_USER = os.getenv("CARTOGRAPH_DB_USER", "cartograph")
DB_PASSWORD = os.getenv("CARTOGRAPH_DB_PASSWORD", "cartograph")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("CARTOGRAPH_EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMS = 1536

TRIGGER_POLL_INTERVAL = float(os.getenv("CARTOGRAPH_TRIGGER_POLL_INTERVAL", "2.0"))
HEARTBEAT_TIMEOUT = int(os.getenv("CARTOGRAPH_HEARTBEAT_TIMEOUT", "300"))

# Invoke-loop concurrency lanes per agent type. Each lane is one dedicated
# worker thread spawning one claude subprocess at a time. Total concurrent
# subprocesses = sum of these. Default: 8 (1+2+1+4).
INVOKE_LANES_ORCH = int(os.getenv("CARTOGRAPH_INVOKE_LANES_ORCH", "1"))
INVOKE_LANES_ITER = int(os.getenv("CARTOGRAPH_INVOKE_LANES_ITER", "2"))
INVOKE_LANES_RES  = int(os.getenv("CARTOGRAPH_INVOKE_LANES_RES",  "1"))
INVOKE_LANES_SME  = int(os.getenv("CARTOGRAPH_INVOKE_LANES_SME",  "4"))

MERGE_CONFIDENCE_THRESHOLD = 0.85
REJECT_CONFIDENCE_THRESHOLD = 0.3


def get_dsn() -> str:
    return f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
