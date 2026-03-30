"""Manifest inspector — inspect, classify, and risk-score MCP servers.

Examines a server's tool manifest to determine capabilities, risk level,
and appropriate trust tier before installation.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

from src.integrations.capabilities import CAPABILITY_CATALOG

logger = logging.getLogger(__name__)

# Risk weights for different capability properties
_RISK_WEIGHTS = {
    "write": 3,
    "delete": 5,
    "send": 4,
    "execute": 5,
    "admin": 5,
    "create": 2,
    "update": 2,
    "read": 1,
    "search": 1,
    "list": 1,
}

# High-risk tool name patterns
_HIGH_RISK_PATTERNS = [
    "delete",
    "remove",
    "drop",
    "send_email",
    "send_message",
    "execute",
    "run_command",
    "transfer",
    "payment",
    "deploy",
    "publish",
]

# Sensitive data patterns in tool descriptions
_SENSITIVE_PATTERNS = [
    "password",
    "secret",
    "token",
    "credential",
    "api_key",
    "private_key",
    "ssh_key",
    "auth",
]


@dataclass(frozen=True)
class ToolClassification:
    name: str
    capability: str | None
    risk_level: str  # low, medium, high, critical
    risk_factors: list[str]
    read_only: bool


@dataclass(frozen=True)
class InspectionResult:
    server_name: str
    tool_count: int
    manifest_hash: str
    tools: list[ToolClassification]
    capabilities: list[str]
    risk_score: int  # 0-100
    risk_factors: list[str]
    recommended_tier: str  # T1, T2, T3
    has_write_tools: bool
    has_sensitive_access: bool
    summary: str


def compute_manifest_hash(tools: list[dict]) -> str:
    """Deterministic hash of tool manifest for change detection."""
    entries = [{"name": t.get("name", ""), "description": t.get("description", "")} for t in tools]
    canonical = json.dumps(
        sorted(entries, key=lambda x: x["name"]),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _get_capability_for_tool(tool_name: str) -> str | None:
    """Look up the canonical capability for a tool name via the catalog."""
    from src.tools.catalog import EXTERNAL_TOOL_SEEDS, INTERNAL_TOOLS

    for tool in INTERNAL_TOOLS:
        if tool.name == tool_name:
            return tool.capability
    for seed in EXTERNAL_TOOL_SEEDS:
        if seed.name == tool_name:
            return seed.capability
    return None


def classify_tool(tool: dict) -> ToolClassification:
    """Classify a single tool's risk level and capability."""
    name = tool.get("name", "")
    description = (tool.get("description", "") or "").lower()
    risk_factors: list[str] = []

    # Check capability mapping via catalog
    capability = _get_capability_for_tool(name)

    # Determine read-only
    read_only = True
    for pattern in ["write", "create", "update", "delete", "send", "execute", "post", "put"]:
        if pattern in name.lower() or pattern in description:
            read_only = False
            break

    # Score risk factors
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern in name.lower():
            risk_factors.append(f"high_risk_action:{pattern}")

    for pattern in _SENSITIVE_PATTERNS:
        if pattern in description:
            risk_factors.append(f"sensitive_data:{pattern}")

    # Check input schema for sensitive fields
    input_schema = tool.get("inputSchema", {})
    if isinstance(input_schema, dict):
        for prop_name in input_schema.get("properties", {}):
            for pattern in _SENSITIVE_PATTERNS:
                if pattern in prop_name.lower():
                    risk_factors.append(f"sensitive_input:{prop_name}")

    # Determine risk level
    if any("sensitive_data" in f for f in risk_factors) or "execute" in name.lower():
        risk_level = "critical"
    elif len(risk_factors) >= 2:
        risk_level = "high"
    elif risk_factors:
        risk_level = "medium"
    else:
        risk_level = "low"

    # Check catalog for known capability risk
    if capability and capability in CAPABILITY_CATALOG:
        meta = CAPABILITY_CATALOG[capability]
        if meta.risk_level == "critical":
            risk_level = "critical"
        elif meta.risk_level == "high" and risk_level not in ("critical",):
            risk_level = "high"

    return ToolClassification(
        name=name,
        capability=capability,
        risk_level=risk_level,
        risk_factors=risk_factors,
        read_only=read_only,
    )


def inspect_manifest(server_name: str, tools: list[dict]) -> InspectionResult:
    """Inspect an MCP server manifest and produce a risk assessment."""
    manifest_hash = compute_manifest_hash(tools)
    classifications = [classify_tool(t) for t in tools]

    capabilities = sorted({c.capability for c in classifications if c.capability})
    has_write = any(not c.read_only for c in classifications)
    has_sensitive = any(any("sensitive" in f for f in c.risk_factors) for c in classifications)

    # Aggregate risk score (0-100)
    if not classifications:
        risk_score = 0
    else:
        tool_scores = []
        for c in classifications:
            score = {"low": 10, "medium": 30, "high": 60, "critical": 90}.get(c.risk_level, 10)
            tool_scores.append(score)
        risk_score = min(100, int(sum(tool_scores) / len(tool_scores) + len(tool_scores) * 2))

    # Aggregate risk factors
    all_factors: list[str] = []
    for c in classifications:
        all_factors.extend(c.risk_factors)
    if has_write:
        all_factors.append("has_write_operations")
    if has_sensitive:
        all_factors.append("accesses_sensitive_data")
    if len(tools) > 20:
        all_factors.append("large_tool_surface")

    unique_factors = sorted(set(all_factors))

    # Recommend trust tier
    if risk_score >= 70 or has_sensitive:
        recommended_tier = "T3"
    elif risk_score >= 40 or has_write:
        recommended_tier = "T2"
    else:
        recommended_tier = "T1"

    # Build summary
    critical_count = sum(1 for c in classifications if c.risk_level == "critical")
    high_count = sum(1 for c in classifications if c.risk_level == "high")
    summary_parts = [f"{len(tools)} tools, risk score {risk_score}/100"]
    if critical_count:
        summary_parts.append(f"{critical_count} critical-risk tools")
    if high_count:
        summary_parts.append(f"{high_count} high-risk tools")
    summary_parts.append(f"recommended tier: {recommended_tier}")

    return InspectionResult(
        server_name=server_name,
        tool_count=len(tools),
        manifest_hash=manifest_hash,
        tools=classifications,
        capabilities=capabilities,
        risk_score=risk_score,
        risk_factors=unique_factors,
        recommended_tier=recommended_tier,
        has_write_tools=has_write,
        has_sensitive_access=has_sensitive,
        summary=", ".join(summary_parts),
    )
