from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES, AGENT_MODEL_TIERS, AGENTS


def test_executor_replaces_operator_in_roster():
    assert AGENTS.get("operator") is None
    ex = AGENTS.get("executor")
    assert ex is not None
    assert AGENT_MODEL_TIERS["executor"] == "sonnet"
    assert "operator" not in AGENT_CAPABILITY_SCOPES
    assert "email.send" in AGENT_CAPABILITY_SCOPES["executor"]


def test_soul_core_no_longer_names_operator():
    from src.orchestrator.prompts import JARVIS_SOUL_CORE

    assert "Operator" not in JARVIS_SOUL_CORE
    assert "Executor" in JARVIS_SOUL_CORE
