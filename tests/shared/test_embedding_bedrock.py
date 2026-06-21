"""Unit tests for Bedrock embedding provider (no live AWS required)."""

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from shared import config
from shared import embedding as emb


@pytest.fixture(autouse=True)
def _bedrock_provider(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "bedrock")
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
    monkeypatch.setattr(config, "EMBEDDING_DIMS", 1024)
    monkeypatch.setattr(config, "AWS_REGION", "us-east-2")
    emb._bedrock_client = None


def test_embed_text_titan_v2_parses_response():
    fake_vec = [0.1] * 1024
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = {
        "body": BytesIO(json.dumps({"embedding": fake_vec}).encode()),
    }
    with patch.object(emb, "_get_bedrock_client", return_value=mock_client):
        out = emb.embed_text("hello service")
    assert out == fake_vec
    body = json.loads(mock_client.invoke_model.call_args.kwargs["body"])
    assert body["inputText"] == "hello service"
    assert body["dimensions"] == 1024
    assert body["normalize"] is True


def test_embed_text_cohere_uses_input_type(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "cohere.embed-english-v3")
    fake_vec = [0.2] * 1024
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = {
        "body": BytesIO(json.dumps({
            "embeddings": [{"embedding_type": "float", "embedding": fake_vec}],
        }).encode()),
    }
    with patch.object(emb, "_get_bedrock_client", return_value=mock_client):
        out = emb.embed_text("query text", input_type="search_query")
    assert out == fake_vec
    body = json.loads(mock_client.invoke_model.call_args.kwargs["body"])
    assert body["input_type"] == "search_query"


def test_embed_text_returns_none_on_invoke_failure():
    mock_client = MagicMock()
    mock_client.invoke_model.side_effect = RuntimeError("access denied")
    with patch.object(emb, "_get_bedrock_client", return_value=mock_client):
        assert emb.embed_text("x") is None


def test_embed_text_empty_string():
    assert emb.embed_text("") is None
    assert emb.embed_text("   ") is None


def test_embed_text_rejects_wrong_dim():
    mock_client = MagicMock()
    mock_client.invoke_model.return_value = {
        "body": BytesIO(json.dumps({"embedding": [0.0] * 512}).encode()),
    }
    with patch.object(emb, "_get_bedrock_client", return_value=mock_client):
        assert emb.embed_text("x") is None
