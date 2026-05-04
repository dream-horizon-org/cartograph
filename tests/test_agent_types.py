import pytest
from agent_management.agent_types.base import AgentTypeConfig, get_config


def test_agent_type_config_dataclass():
    config = AgentTypeConfig(
        agent_type="orchestrator",
        allowed_tools=["bash", "Read"],
        mcp_servers=["cartograph-db"],
        system_prompt="You are the orchestrator.",
        priority=100,
        can_install=False,
    )
    assert config.agent_type == "orchestrator"
    assert config.priority == 100
    assert config.can_install is False


def test_get_config_orchestrator():
    config = get_config("orchestrator", agent_id="orch-test")
    assert config.agent_type == "orchestrator"
    assert config.priority == 100
    assert config.can_install is False
    assert "bash" in config.allowed_tools
    assert "cartograph-db" in config.mcp_servers
    assert "Orchestrator" in config.system_prompt


def test_get_config_iterator_with_plane():
    config = get_config("iterator", plane="github")
    assert config.agent_type == "iterator"
    assert config.priority == 60
    assert config.can_install is True
    assert "github" in config.system_prompt


def test_get_config_sme_with_resource():
    config = get_config(
        "sme", plane="github", resource_id="repo-xyz", agent_id="sme-test"
    )
    assert config.agent_type == "sme"
    assert config.priority == 40
    assert config.can_install is False
    assert "repo-xyz" in config.system_prompt
    assert "github" in config.system_prompt


def test_get_config_resolver_raises():
    with pytest.raises(ValueError, match="Unknown agent type"):
        get_config("resolver")


def test_get_config_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown agent type"):
        get_config("unknown")
