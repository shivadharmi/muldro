from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES, AGENT_MODEL_TIERS, AGENTS


def test_executor_replaces_operator_in_roster():
    assert AGENTS.get("operator") is None
    ex = AGENTS.get("executor")
    assert ex is not None
    assert AGENT_MODEL_TIERS["executor"] == "balanced"
    assert "operator" not in AGENT_CAPABILITY_SCOPES
    assert "email.send" in AGENT_CAPABILITY_SCOPES["executor"]


def test_no_prompt_still_names_the_operator():
    """Rename fence (Operator -> Executor), retargeted when the agent roster left the
    shared soul core. The core is now identity + behavioural law only, so the agent's name
    lives in the one prompt whose reader IS that agent — see
    ``tests/test_soul_core_consistency.py`` for why the roster moved."""
    from src.orchestrator.prompts import AGENT_PROMPTS, EXECUTOR_PROMPT, MULDRO_SOUL_CORE

    assert "Operator" not in MULDRO_SOUL_CORE
    for name, prompt in AGENT_PROMPTS.items():
        assert "Operator" not in prompt, f"{name} prompt still names the Operator"
    assert "Executor" in EXECUTOR_PROMPT
