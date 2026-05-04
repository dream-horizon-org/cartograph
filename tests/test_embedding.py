import pytest
from unittest.mock import MagicMock, patch

from agent_management.embedding import synthesize_description, embed_text


@pytest.fixture(autouse=True)
def clear_openai_cache():
    """Clear the lru_cache before and after each test for isolation."""
    from agent_management import embedding
    embedding._get_openai_client.cache_clear()
    yield
    embedding._get_openai_client.cache_clear()


def test_synthesize_description_minimal():
    component = {"component_type": "application", "canonical_name": "feeds-api"}
    attributions = []
    result = synthesize_description(component, attributions)
    assert "application" in result
    assert "feeds-api" in result


def test_synthesize_description_with_attributions():
    component = {"component_type": "application", "canonical_name": "feeds-api"}
    attributions = [
        {"resource_type": "hostname",      "identifier": "feeds-api.dream11.local"},
        {"resource_type": "entry_point",   "identifier": "FeedsApplication.java"},
        {"resource_type": "runtime",       "identifier": "java"},
        {"resource_type": "endpoint",      "identifier": "GET /v2/feeds/{userId}"},
        {"resource_type": "endpoint",      "identifier": "POST /v2/feeds/refresh"},
    ]
    result = synthesize_description(component, attributions)
    assert "feeds-api.dream11.local" in result
    assert "FeedsApplication.java" in result
    assert "java" in result
    assert "GET /v2/feeds/{userId}" in result


def test_synthesize_description_prioritises_discriminating_fields():
    component = {"component_type": "application", "canonical_name": "svc"}
    attributions = [
        {"resource_type": "region",      "identifier": "ap-south-1"},
        {"resource_type": "entry_point", "identifier": "Main.java"},
    ]
    result = synthesize_description(component, attributions)
    # entry_point should appear before region in the output
    assert result.index("Main.java") < result.index("ap-south-1")


def test_embed_text_calls_openai():
    fake_vector = [0.1] * 1536
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=fake_vector)]

    with patch("agent_management.embedding._get_openai_client") as mock_client:
        instance = mock_client.return_value
        instance.embeddings.create.return_value = mock_response
        result = embed_text("hello world")

    assert result == fake_vector
    instance.embeddings.create.assert_called_once()


def test_embed_text_uses_configured_model():
    fake_vector = [0.0] * 1536
    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=fake_vector)]

    with patch("agent_management.embedding._get_openai_client") as mock_client:
        instance = mock_client.return_value
        instance.embeddings.create.return_value = mock_response
        embed_text("test")

    call_kwargs = instance.embeddings.create.call_args
    assert call_kwargs.kwargs["model"] == "text-embedding-3-small"
