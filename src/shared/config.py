"""Shared configuration — reads from environment variables."""

import json
import os


# Pre-load env from CARTOGRAPH_AGENT_SETTINGS_PATH file (Bedrock isolation).
# When set, the file's `env` block populates os.environ so the rest of this
# module reads model ids / region / token consistently from one source. Real
# shell env vars take precedence (setdefault), so explicit overrides still
# work. Unset → no-op, host falls back to defaults below.
_settings_path = os.getenv("CARTOGRAPH_AGENT_SETTINGS_PATH")
if _settings_path and os.path.isfile(_settings_path):
    try:
        with open(_settings_path) as _f:
            _env_block = json.load(_f).get("env") or {}
        for _k, _v in _env_block.items():
            if _v is not None:
                os.environ.setdefault(_k, str(_v))
    except (json.JSONDecodeError, OSError):
        # Bad file → silently ignore; falls back to defaults. Caller debugs.
        pass


DB_HOST = os.getenv("CARTOGRAPH_DB_HOST", "localhost")
DB_PORT = int(os.getenv("CARTOGRAPH_DB_PORT", "5432"))
DB_NAME = os.getenv("CARTOGRAPH_DB_NAME", "cartograph")
DB_USER = os.getenv("CARTOGRAPH_DB_USER", "cartograph")
DB_PASSWORD = os.getenv("CARTOGRAPH_DB_PASSWORD", "cartograph")

# Embedding provider: ollama (default) or bedrock (production).
# Bedrock uses boto3 + standard AWS credentials (instance role, env keys,
# or ~/.aws/credentials). CARTOGRAPH_AGENT_SETTINGS_PATH may set AWS_REGION.
EMBEDDING_PROVIDER = os.getenv(
    "CARTOGRAPH_EMBEDDING_PROVIDER", "ollama"
).strip().lower()
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-2"))
OLLAMA_URL = os.getenv("CARTOGRAPH_OLLAMA_URL", "http://localhost:11434")
_DEFAULT_BEDROCK_EMBED_MODEL = "amazon.titan-embed-text-v2:0"
_DEFAULT_OLLAMA_EMBED_MODEL = "mxbai-embed-large"
EMBEDDING_MODEL = os.getenv(
    "CARTOGRAPH_EMBEDDING_MODEL",
    _DEFAULT_BEDROCK_EMBED_MODEL
    if EMBEDDING_PROVIDER == "bedrock"
    else _DEFAULT_OLLAMA_EMBED_MODEL,
)
EMBEDDING_DIMS = int(os.getenv("CARTOGRAPH_EMBEDDING_DIMS", "1024"))

TRIGGER_POLL_INTERVAL = float(os.getenv("CARTOGRAPH_TRIGGER_POLL_INTERVAL", "2.0"))
HEARTBEAT_TIMEOUT = int(os.getenv("CARTOGRAPH_HEARTBEAT_TIMEOUT", "300"))

# Invoke-loop concurrency lanes per agent type. Each lane is one dedicated
# worker thread spawning one claude subprocess at a time. Total concurrent
# subprocesses = sum of these. Default: 16 (1+2+1+12 — Phase 10.11 bumped
# SME 4→8; Phase 10.13.13 bumped 8→12 for the 4th real-data run).
INVOKE_LANES_ORCH = int(os.getenv("CARTOGRAPH_INVOKE_LANES_ORCH", "1"))
INVOKE_LANES_ITER = int(os.getenv("CARTOGRAPH_INVOKE_LANES_ITER", "2"))
INVOKE_LANES_RES  = int(os.getenv("CARTOGRAPH_INVOKE_LANES_RES",  "1"))
INVOKE_LANES_SME  = int(os.getenv("CARTOGRAPH_INVOKE_LANES_SME",  "12"))

# Claude model ids passed to `claude -p --model <id>`. On Bedrock (when
# CLAUDE_CODE_USE_BEDROCK=1 is set) these MUST be Bedrock inference
# profile ids (e.g. `us.anthropic.claude-opus-4-7[1m]`), not Anthropic
# API aliases (`claude-opus-4-6`). Env vars mirror the standard
# ANTHROPIC_DEFAULT_*_MODEL names so a single source-of-truth controls
# both Claude Code's own model and our agents' models.
MODEL_OPUS = os.getenv(
    "CARTOGRAPH_MODEL_OPUS",
    os.getenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-6"),
)
MODEL_SONNET = os.getenv(
    "CARTOGRAPH_MODEL_SONNET",
    os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6"),
)

MERGE_CONFIDENCE_THRESHOLD = 0.85
REJECT_CONFIDENCE_THRESHOLD = 0.3


def get_dsn() -> str:
    return f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
