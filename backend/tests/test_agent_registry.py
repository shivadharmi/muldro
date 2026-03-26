"""Tests for AgentRegistry — seed, CRUD, load_as_sub_agents."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.models.agents import Agent
from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES, AGENT_MODEL_TIERS, SubAgent
from src.orchestrator.prompts import AGENT_PROMPTS
from src.services.agent_registry import AgentRegistry


class FakeResult:
    """Mimics SQLAlchemy result objects for unit tests."""

    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._scalar


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_seed_defaults_creates_eight_agents(mock_db):
    """seed_defaults should create 8 agents when table is empty."""
    # No existing agents
    mock_db.execute = AsyncMock(return_value=FakeResult(rows=[]))

    registry = AgentRegistry(mock_db)
    count = await registry.seed_defaults()

    assert count == 8
    assert mock_db.add.call_count == 8

    # Verify agent names match AGENT_PROMPTS
    added_names = set()
    for call in mock_db.add.call_args_list:
        agent = call[0][0]
        assert isinstance(agent, Agent)
        assert agent.agent_id.startswith("agt_")
        assert agent.name in AGENT_PROMPTS
        added_names.add(agent.name)

    assert added_names == set(AGENT_PROMPTS.keys())


@pytest.mark.asyncio
async def test_seed_defaults_skips_existing(mock_db):
    """seed_defaults should skip agents that already exist (with matching scope/prompt)."""
    # Simulate 5 existing agents with correct scope/prompt — no updates needed
    existing_agents = []
    for name in ["observer", "librarian", "planner", "governor", "operator"]:
        agent = MagicMock(spec=Agent)
        agent.name = name
        agent.capability_scope = sorted(AGENT_CAPABILITY_SCOPES.get(name, set()))
        agent.system_prompt = AGENT_PROMPTS[name]
        existing_agents.append(agent)

    mock_db.execute = AsyncMock(return_value=FakeResult(rows=existing_agents))

    registry = AgentRegistry(mock_db)
    count = await registry.seed_defaults()

    # Should only create the 3 missing: presenter, researcher, persona
    assert count == 3


@pytest.mark.asyncio
async def test_seed_defaults_model_tiers(mock_db):
    """seed_defaults should assign correct model tiers from AGENT_MODEL_TIERS."""
    mock_db.execute = AsyncMock(return_value=FakeResult(rows=[]))

    registry = AgentRegistry(mock_db)
    await registry.seed_defaults()

    for call in mock_db.add.call_args_list:
        agent = call[0][0]
        expected_tier = AGENT_MODEL_TIERS.get(agent.name, "sonnet")
        assert agent.model_tier == expected_tier, f"{agent.name} should be {expected_tier}"


@pytest.mark.asyncio
async def test_seed_defaults_capability_scopes(mock_db):
    """seed_defaults should assign correct capability scopes."""
    mock_db.execute = AsyncMock(return_value=FakeResult(rows=[]))

    registry = AgentRegistry(mock_db)
    await registry.seed_defaults()

    for call in mock_db.add.call_args_list:
        agent = call[0][0]
        expected = sorted(AGENT_CAPABILITY_SCOPES.get(agent.name, set()))
        assert agent.capability_scope == expected, f"{agent.name} capability scope mismatch"


@pytest.mark.asyncio
async def test_create_agent(mock_db):
    """create_agent should create a new agent with correct fields."""
    mock_db.execute = AsyncMock(return_value=FakeResult())

    registry = AgentRegistry(mock_db)
    agent = await registry.create_agent(
        name="custom_agent",
        display_name="Custom Agent",
        system_prompt="You are a custom agent.",
        model_tier="haiku",
        capability_scope=["internal.search_memory", "internal.get_entities"],
        max_tokens=2048,
        temperature=0.5,
    )

    assert agent.agent_id.startswith("agt_")
    assert agent.name == "custom_agent"
    assert agent.display_name == "Custom Agent"
    assert agent.model_tier == "haiku"
    assert agent.capability_scope == ["internal.get_entities", "internal.search_memory"]  # sorted
    assert agent.max_tokens == 2048
    assert agent.temperature == 0.5
    assert agent.enabled is True


@pytest.mark.asyncio
async def test_load_as_sub_agents(mock_db):
    """load_as_sub_agents should return SubAgent instances keyed by name."""
    mock_agent = MagicMock(spec=Agent)
    mock_agent.name = "planner"
    mock_agent.system_prompt = "Plan things"
    mock_agent.model_tier = "opus"
    mock_agent.capability_scope = ["internal.plan_command", "internal.search_memory"]
    mock_agent.max_tokens = 8192
    mock_agent.temperature = 0.3
    mock_agent.enabled = True

    result = FakeResult()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_agent])))
    mock_db.execute = AsyncMock(return_value=result)

    registry = AgentRegistry(mock_db)
    agents = await registry.load_as_sub_agents()

    assert "planner" in agents
    assert isinstance(agents["planner"], SubAgent)
    assert agents["planner"].model_tier == "opus"
    assert agents["planner"].capability_scope == {"internal.plan_command", "internal.search_memory"}
    assert agents["planner"].max_tokens == 8192


@pytest.mark.asyncio
async def test_planner_gets_opus_and_governor_low_temp(mock_db):
    """Planner should get Opus model and Governor 0.1 temperature."""
    mock_db.execute = AsyncMock(return_value=FakeResult(rows=[]))

    registry = AgentRegistry(mock_db)
    await registry.seed_defaults()

    agents_by_name = {}
    for call in mock_db.add.call_args_list:
        agent = call[0][0]
        agents_by_name[agent.name] = agent

    assert agents_by_name["planner"].model_tier == "opus"
    assert agents_by_name["planner"].max_tokens == 8192
    assert agents_by_name["governor"].temperature == 0.1
    assert agents_by_name["persona"].model_tier == "haiku"
