"""Tests for manifest inspector."""


class TestManifestInspector:
    def test_classify_read_only_tool(self):
        from src.integrations.manifest_inspector import classify_tool

        result = classify_tool({"name": "list_files", "description": "List files in directory"})
        assert result.read_only is True
        assert result.risk_level == "low"

    def test_classify_write_tool(self):
        from src.integrations.manifest_inspector import classify_tool

        result = classify_tool({"name": "delete_file", "description": "Delete a file"})
        assert result.read_only is False
        assert result.risk_level in ("medium", "high", "critical")
        assert any("delete" in f for f in result.risk_factors)

    def test_classify_sensitive_tool(self):
        from src.integrations.manifest_inspector import classify_tool

        result = classify_tool(
            {
                "name": "get_config",
                "description": "Get configuration including password and token",
            }
        )
        assert result.risk_level in ("high", "critical")
        assert any("sensitive" in f for f in result.risk_factors)

    def test_classify_tool_with_sensitive_input(self):
        from src.integrations.manifest_inspector import classify_tool

        result = classify_tool(
            {
                "name": "authenticate",
                "description": "Authenticate user",
                "inputSchema": {
                    "properties": {"api_key": {"type": "string"}},
                },
            }
        )
        assert any("sensitive_input" in f for f in result.risk_factors)

    def test_inspect_empty_manifest(self):
        from src.integrations.manifest_inspector import inspect_manifest

        result = inspect_manifest("test-server", [])
        assert result.tool_count == 0
        assert result.risk_score == 0
        assert result.recommended_tier == "T1"

    def test_inspect_low_risk_manifest(self):
        from src.integrations.manifest_inspector import inspect_manifest

        tools = [
            {"name": "search_docs", "description": "Search documents"},
            {"name": "list_items", "description": "List items"},
        ]
        result = inspect_manifest("test-server", tools)
        assert result.tool_count == 2
        assert result.risk_score < 50
        assert result.has_write_tools is False

    def test_inspect_high_risk_manifest(self):
        from src.integrations.manifest_inspector import inspect_manifest

        tools = [
            {"name": "delete_data", "description": "Delete all data permanently"},
            {"name": "send_email", "description": "Send email with password reset"},
            {"name": "execute_command", "description": "Execute shell command"},
        ]
        result = inspect_manifest("risky-server", tools)
        assert result.risk_score >= 50
        assert result.has_write_tools is True
        assert result.recommended_tier == "T3"

    def test_compute_manifest_hash_deterministic(self):
        from src.integrations.manifest_inspector import compute_manifest_hash

        tools = [
            {"name": "b_tool", "description": "Second"},
            {"name": "a_tool", "description": "First"},
        ]
        hash1 = compute_manifest_hash(tools)
        hash2 = compute_manifest_hash(list(reversed(tools)))
        assert hash1 == hash2  # order-independent

    def test_compute_manifest_hash_changes(self):
        from src.integrations.manifest_inspector import compute_manifest_hash

        tools1 = [{"name": "tool_a", "description": "Desc A"}]
        tools2 = [{"name": "tool_a", "description": "Desc B"}]
        assert compute_manifest_hash(tools1) != compute_manifest_hash(tools2)
