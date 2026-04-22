"""Shared configuration — reads from environment variables."""

import os


DB_HOST = os.getenv("CARTOGRAPH_DB_HOST", "localhost")
DB_PORT = int(os.getenv("CARTOGRAPH_DB_PORT", "5432"))
DB_NAME = os.getenv("CARTOGRAPH_DB_NAME", "cartograph")
DB_USER = os.getenv("CARTOGRAPH_DB_USER", "cartograph")
DB_PASSWORD = os.getenv("CARTOGRAPH_DB_PASSWORD", "cartograph")

# Embedding: Ollama runs locally on Apple Silicon (Metal GPU), no API key
# needed. `mxbai-embed-large` is a 1024d top-tier English embedder and is
# the default; override with CARTOGRAPH_EMBEDDING_MODEL if you pull a
# different one. Warm embed latency ~40-60ms on M-series.
OLLAMA_URL = os.getenv("CARTOGRAPH_OLLAMA_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.getenv("CARTOGRAPH_EMBEDDING_MODEL", "mxbai-embed-large")
EMBEDDING_DIMS = int(os.getenv("CARTOGRAPH_EMBEDDING_DIMS", "1024"))

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
