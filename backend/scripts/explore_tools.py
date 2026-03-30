#!/usr/bin/env python3
"""Explore all MCP tool schemas, definitions, and names by loading servers.

This script does NOT read static files — it loads and introspects the actual
MCP servers (internal FastMCP + external MCP) to report their real tool names,
schemas, and metadata.

Usage:
    cd backend/
    python scripts/explore_tools.py
    python scripts/explore_tools.py --internal-only   # skip external servers
    python scripts/explore_tools.py --external-only   # skip internal servers
    python scripts/explore_tools.py --json             # JSON output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Add project root to sys.path
_backend = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_backend))

# Prevent DB/Redis connections and MCP subprocess spawning
os.environ["PYTEST_CURRENT_TEST"] = "explore_tools"
os.environ["JARVIS_SKIP_MCP_BRIDGE"] = "1"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────


@dataclass
class ToolInfo:
    name: str
    description: str = ""
    server: str = ""
    namespace: str = ""
    tags: list[str] = field(default_factory=list)
    annotations: dict[str, Any] = field(default_factory=dict)
    input_schema: dict[str, Any] = field(default_factory=dict)
    canonical_name: str = ""
    capability: str = ""
    risk_level: str = ""
    requires_approval: bool = False


@dataclass
class ServerReport:
    server_name: str
    server_type: str  # "internal_fastmcp" or "external_mcp"
    transport: str = ""
    tool_count: int = 0
    tools: list[ToolInfo] = field(default_factory=list)
    error: str = ""


# ── Internal FastMCP servers ──────────────────────────────────────────


async def explore_internal_servers() -> list[ServerReport]:
    """Load internal FastMCP servers and list their tools via the MCP protocol."""
    reports: list[ServerReport] = []

    # 1. Intelligence server (standalone, no namespace)
    try:
        from src.tools.intelligence_server import intelligence

        report = await _explore_fastmcp_server(intelligence, "intelligence", standalone=True)
        reports.append(report)
    except Exception as e:
        reports.append(
            ServerReport(
                server_name="intelligence",
                server_type="internal_fastmcp",
                error=str(e),
            )
        )

    # 2. Communication server (standalone, no namespace)
    try:
        from src.tools.communication_server import communication

        report = await _explore_fastmcp_server(communication, "communication", standalone=True)
        reports.append(report)
    except Exception as e:
        reports.append(
            ServerReport(
                server_name="communication",
                server_type="internal_fastmcp",
                error=str(e),
            )
        )

    # 3. Composed jarvis_tools server (with namespaces)
    try:
        from src.tools.server import jarvis_tools

        report = await _explore_fastmcp_server(jarvis_tools, "jarvis-tools", standalone=False)
        reports.append(report)
    except Exception as e:
        reports.append(
            ServerReport(
                server_name="jarvis-tools",
                server_type="internal_fastmcp",
                error=str(e),
            )
        )

    return reports


async def _explore_fastmcp_server(
    server: Any, name: str, standalone: bool
) -> ServerReport:
    """Use fastmcp Client to connect to a FastMCP server and list_tools."""
    from fastmcp import Client

    report = ServerReport(
        server_name=name,
        server_type="internal_fastmcp",
        transport="in-process",
    )

    try:
        async with Client(server) as client:
            raw_tools = await client.list_tools()
            report.tool_count = len(raw_tools)

            for t in raw_tools:
                input_schema = (
                    getattr(t, "inputSchema", None)
                    or getattr(t, "input_schema", None)
                    or {}
                )
                # Convert to dict if it's a Pydantic model
                if hasattr(input_schema, "model_dump"):
                    input_schema = input_schema.model_dump()

                annotations_raw = getattr(t, "annotations", None)
                annotations_dict = {}
                if annotations_raw:
                    if hasattr(annotations_raw, "model_dump"):
                        annotations_dict = annotations_raw.model_dump(exclude_none=True)
                    elif isinstance(annotations_raw, dict):
                        annotations_dict = annotations_raw

                # Determine namespace from the tool name
                ns = ""
                if "_" in t.name and not standalone:
                    parts = t.name.split("_", 1)
                    if parts[0] in ("intelligence", "communication"):
                        ns = parts[0]

                tool_info = ToolInfo(
                    name=t.name,
                    description=(t.description or "")[:200],
                    server=name,
                    namespace=ns,
                    annotations=annotations_dict,
                    input_schema=input_schema,
                )
                report.tools.append(tool_info)

    except Exception as e:
        report.error = str(e)

    return report


# ── External MCP servers ──────────────────────────────────────────────


async def explore_external_servers() -> list[ServerReport]:
    """Try to launch and introspect each external MCP server.

    Each server is launched as a subprocess (stdio transport), list_tools
    is called, then the server is shut down. This requires the server
    packages to be installed (npx, docker, uvx).
    """
    from src.integrations.seed_installations import _DEFAULT_INSTALLATIONS

    reports: list[ServerReport] = []

    for inst_data in _DEFAULT_INSTALLATIONS:
        server_name = inst_data["server_name"]
        command = inst_data.get("command", "")
        args = inst_data.get("args", [])
        transport = inst_data.get("transport", "stdio")

        report = ServerReport(
            server_name=server_name,
            server_type="external_mcp",
            transport=transport,
        )

        if not command:
            report.error = "No command defined"
            reports.append(report)
            continue

        # Check if env vars are available (but don't require them — some servers
        # can start without tokens, they just fail on tool calls)
        env_template = inst_data.get("env_template", {})
        missing_env = [k for k in env_template if not os.environ.get(k)]

        try:
            report = await _explore_external_server(
                server_name, command, args, env_template, transport, missing_env
            )
        except Exception as e:
            report.error = f"Failed to connect: {e}"

        reports.append(report)

    return reports


async def _explore_external_server(
    server_name: str,
    command: str,
    args: list[str],
    env_template: dict,
    transport: str,
    missing_env: list[str],
) -> ServerReport:
    """Launch an external MCP server, list its tools, then shut down."""
    from fastmcp import Client

    report = ServerReport(
        server_name=server_name,
        server_type="external_mcp",
        transport=transport,
    )

    if transport != "stdio":
        report.error = f"Transport '{transport}' not supported for local probing"
        return report

    # Build the MCP config for the Client
    env_resolved = {}
    for k in env_template:
        val = os.environ.get(k, "")
        if val:
            env_resolved[k] = val

    config: dict = {
        "command": command,
        "args": args,
    }
    if env_resolved:
        config["env"] = env_resolved

    mcp_config = {"mcpServers": {server_name: config}}

    if missing_env:
        report.error = f"Missing env vars: {', '.join(missing_env)} — attempting anyway"

    try:
        # Apply a reasonable timeout for server startup
        async with asyncio.timeout(30):
            async with Client(mcp_config) as client:
                raw_tools = await client.list_tools()
                report.tool_count = len(raw_tools)

                # Load capability mapping
                from src.integrations.capabilities import TOOL_TO_CAPABILITY

                for t in raw_tools:
                    input_schema = (
                        getattr(t, "inputSchema", None)
                        or getattr(t, "input_schema", None)
                        or {}
                    )
                    if hasattr(input_schema, "model_dump"):
                        input_schema = input_schema.model_dump()

                    capability = TOOL_TO_CAPABILITY.get(t.name, "")

                    tool_info = ToolInfo(
                        name=t.name,
                        description=(t.description or "")[:200],
                        server=server_name,
                        canonical_name=t.name,  # No normalization needed
                        capability=capability,
                        input_schema=input_schema,
                    )
                    report.tools.append(tool_info)

                # Clear the error if we succeeded despite missing env
                if report.error and report.tool_count > 0:
                    report.error = f"(warning: {report.error}) — tools loaded OK"

    except TimeoutError:
        report.error = "Timeout (30s) — server did not start"
    except Exception as e:
        error_str = str(e)[:300]
        if report.error:
            report.error = f"{report.error} | Launch error: {error_str}"
        else:
            report.error = error_str

    return report


# ── Cross-referencing with static registries ──────────────────────────


def cross_reference(reports: list[ServerReport]) -> dict:
    """Cross-reference discovered tools against static registries.

    Returns a summary of:
    - Tools found in MCP but NOT in tool_registry
    - Tools in tool_registry but NOT found in any MCP server
    - Tools with capability mapping gaps
    - Tools with schema but no capability
    """
    from src.integrations.capabilities import TOOL_TO_CAPABILITY
    from src.tools.schemas import TOOL_INPUT_MODELS
    from src.services.tool_registry import _DEFAULT_TOOLS

    # Collect all discovered tool names
    discovered: dict[str, str] = {}  # tool_name → server
    for report in reports:
        for tool in report.tools:
            discovered[tool.name] = report.server_name
            if tool.canonical_name:
                discovered[tool.canonical_name] = report.server_name

    # Collect all registered tool names
    registered = {t["name"] for t in _DEFAULT_TOOLS}

    # Tools discovered but not in registry
    discovered_not_registered = {
        name: server
        for name, server in discovered.items()
        if name not in registered
    }

    # Tools in registry tagged "internal" that should match MCP tools
    internal_registered = {
        t["name"] for t in _DEFAULT_TOOLS if t.get("connector_type") == "internal"
    }

    # Check capability coverage
    no_capability = [
        name for name in discovered if name not in TOOL_TO_CAPABILITY
    ]

    # Check schema coverage (only applies to internal tools)
    no_schema = [
        name
        for name in internal_registered
        if name not in TOOL_INPUT_MODELS
        and name not in ("push_ui_update", "send_telegram", "send_approval_prompt",
                         "get_goal_memories")
    ]

    return {
        "total_discovered": len(discovered),
        "total_registered": len(registered),
        "total_capabilities": len(TOOL_TO_CAPABILITY),
        "total_schemas": len(TOOL_INPUT_MODELS),
        "discovered_not_registered": discovered_not_registered,
        "no_capability_mapping": no_capability,
        "internal_no_schema": no_schema,
    }


# ── Display ──────────────────────────────────────────────────────────


def print_report(reports: list[ServerReport], xref: dict, as_json: bool = False) -> None:
    """Pretty-print or JSON-dump the exploration results."""
    if as_json:
        output = {
            "servers": [asdict(r) for r in reports],
            "cross_reference": xref,
        }
        print(json.dumps(output, indent=2, default=str))
        return

    sep = "=" * 80
    thin = "-" * 80

    print(f"\n{sep}")
    print("  JARVIS MCP TOOL EXPLORATION — LIVE SERVER INTROSPECTION")
    print(f"{sep}\n")

    for report in reports:
        icon = "🔧" if report.server_type == "internal_fastmcp" else "🌐"
        status = f"✅ {report.tool_count} tools" if report.tool_count > 0 else "❌ no tools"
        print(f"{icon} {report.server_name} [{report.server_type}] [{report.transport}] — {status}")

        if report.error:
            print(f"   ⚠️  {report.error}")

        if report.tools:
            print(f"   {thin}")
            # Group by namespace if present
            namespaces: dict[str, list[ToolInfo]] = {}
            for t in report.tools:
                ns_key = t.namespace or "(root)"
                namespaces.setdefault(ns_key, []).append(t)

            for ns, tools in sorted(namespaces.items()):
                if ns != "(root)":
                    print(f"   📁 Namespace: {ns}")
                for t in sorted(tools, key=lambda x: x.name):
                    desc_short = t.description[:80] + "..." if len(t.description) > 80 else t.description
                    print(f"      • {t.name}")
                    if desc_short:
                        print(f"        desc: {desc_short}")
                    if t.canonical_name and t.canonical_name != t.name:
                        print(f"        canonical: {t.canonical_name}")
                    if t.capability:
                        print(f"        capability: {t.capability}")
                    if t.annotations:
                        print(f"        annotations: {t.annotations}")
                    # Show input schema properties (just the keys)
                    props = t.input_schema.get("properties", {})
                    required = t.input_schema.get("required", [])
                    if props:
                        param_strs = []
                        if isinstance(props, dict):
                            for pname, pdef in props.items():
                                ptype = pdef.get("type", "any") if isinstance(pdef, dict) else "any"
                                marker = "*" if pname in required else ""
                                param_strs.append(f"{pname}{marker}:{ptype}")
                        elif isinstance(props, list):
                            for pname in props:
                                marker = "*" if pname in required else ""
                                param_strs.append(f"{pname}{marker}")
                        print(f"        params: {', '.join(param_strs)}")
                    print()
        print()

    # Cross-reference summary
    print(f"\n{sep}")
    print("  CROSS-REFERENCE SUMMARY")
    print(f"{sep}\n")

    print(f"  Total tools discovered (live):    {xref['total_discovered']}")
    print(f"  Total tool_registry entries:       {xref['total_registered']}")
    print(f"  Total capability mappings:         {xref['total_capabilities']}")
    print(f"  Total Pydantic tool schemas:       {xref['total_schemas']}")

    dnr = xref["discovered_not_registered"]
    if dnr:
        print(f"\n  📋 Discovered but NOT in tool_registry ({len(dnr)}):")
        for name, server in sorted(dnr.items()):
            print(f"     • {name} (from {server})")

    ncp = xref["no_capability_mapping"]
    if ncp:
        print(f"\n  📋 No capability mapping ({len(ncp)}):")
        for name in sorted(ncp):
            print(f"     • {name}")

    ins = xref["internal_no_schema"]
    if ins:
        print(f"\n  📋 Internal tools without Pydantic schema ({len(ins)}):")
        for name in sorted(ins):
            print(f"     • {name}")

    print(f"\n{sep}\n")


# ── Also explore the tool_schemas (Pydantic → Claude API format) ─────


def explore_tool_schemas() -> ServerReport:
    """Load tool schemas from tool_schemas.py and report what Claude sees."""
    from src.tools.schemas import TOOL_INPUT_MODELS, build_tool_definitions

    report = ServerReport(
        server_name="tool-schemas (Claude API format)",
        server_type="claude_api_tools",
        transport="in-memory",
    )

    tool_defs = build_tool_definitions()
    report.tool_count = len(tool_defs)

    for td in tool_defs:
        tool_info = ToolInfo(
            name=td["name"],
            description=(td.get("description", ""))[:200],
            server="tool_schemas.py",
            input_schema=td.get("input_schema", {}),
        )
        report.tools.append(tool_info)

    return report


# ── Also explore the ToolRegistry defaults ────────────────────────────


def explore_tool_registry() -> ServerReport:
    """Load the static _DEFAULT_TOOLS from tool_registry.py."""
    from src.integrations.capabilities import TOOL_TO_CAPABILITY
    from src.services.tool_registry import CANONICAL_ALIASES, _DEFAULT_TOOLS

    report = ServerReport(
        server_name="tool-registry (_DEFAULT_TOOLS)",
        server_type="db_seed_registry",
        transport="static",
    )

    seen: set[str] = set()
    for t in _DEFAULT_TOOLS:
        name = t["name"]
        if name in seen:
            continue
        seen.add(name)

        tool_info = ToolInfo(
            name=name,
            server="tool_registry.py",
            risk_level=t.get("risk_level", "low"),
            requires_approval=t.get("requires_approval", False),
            canonical_name=t.get("canonical_name") or CANONICAL_ALIASES.get(name, ""),
            capability=TOOL_TO_CAPABILITY.get(name, ""),
        )
        report.tools.append(tool_info)

    report.tool_count = len(report.tools)
    return report


# ── Also explore the capability catalog ───────────────────────────────


def explore_capabilities() -> ServerReport:
    """Report the TOOL_TO_CAPABILITY mapping."""
    from src.integrations.capabilities import TOOL_TO_CAPABILITY

    report = ServerReport(
        server_name="capabilities (TOOL_TO_CAPABILITY)",
        server_type="capability_mapping",
        transport="static",
    )

    for tool_name, capability in sorted(TOOL_TO_CAPABILITY.items()):
        tool_info = ToolInfo(
            name=tool_name,
            capability=capability,
            server="capabilities.py",
        )
        report.tools.append(tool_info)

    report.tool_count = len(report.tools)
    return report


# ── Agent capability scopes ───────────────────────────────────────────


def explore_agent_scopes() -> dict[str, list[str]]:
    """Load and return agent capability scopes."""
    from src.orchestrator.agents import AGENT_CAPABILITY_SCOPES

    return {agent: sorted(caps) for agent, caps in AGENT_CAPABILITY_SCOPES.items()}


# ── Main ──────────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(description="Explore Jarvis MCP tool inventory")
    parser.add_argument("--internal-only", action="store_true", help="Only explore internal FastMCP")
    parser.add_argument("--external-only", action="store_true", help="Only explore external MCP")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--with-schemas", action="store_true", help="Include full input schemas")
    parser.add_argument("--agents", action="store_true", help="Show agent capability scopes")
    args = parser.parse_args()

    reports: list[ServerReport] = []

    def log(msg: str) -> None:
        if not args.json:
            print(msg)

    # Static registries (always loaded — no network/process needed)
    if not args.external_only:
        log("📦 Loading tool schemas (Pydantic → Claude API)...")
        reports.append(explore_tool_schemas())

    log("📦 Loading tool registry (_DEFAULT_TOOLS)...")
    reports.append(explore_tool_registry())

    log("📦 Loading capability catalog (TOOL_TO_CAPABILITY)...")
    reports.append(explore_capabilities())

    # Internal FastMCP servers
    if not args.external_only:
        log("\n🔧 Loading internal FastMCP servers (in-process)...")
        internal = await explore_internal_servers()
        reports.extend(internal)

    # External MCP servers
    if not args.internal_only:
        log("\n🌐 Probing external MCP servers (subprocess launch)...")
        log("   (This may take a moment per server — 30s timeout each)\n")
        external = await explore_external_servers()
        reports.extend(external)

    # Strip input schemas from output if not requested
    if not args.with_schemas:
        for r in reports:
            for t in r.tools:
                if t.input_schema:
                    props = t.input_schema.get("properties", {})
                    t.input_schema = {
                        "property_count": len(props),
                        "properties": list(props.keys()),
                        "required": t.input_schema.get("required", []),
                    }

    # Cross-reference
    xref = cross_reference(reports)

    # Agent scopes
    if args.agents:
        scopes = explore_agent_scopes()
        xref["agent_capability_scopes"] = scopes

    # Output
    print_report(reports, xref, as_json=args.json)

    if args.agents and not args.json:
        sep = "=" * 80
        print(f"\n{sep}")
        print("  AGENT CAPABILITY SCOPES")
        print(f"{sep}\n")
        for agent, caps in sorted(explore_agent_scopes().items()):
            print(f"  🤖 {agent} ({len(caps)} capabilities):")
            for cap in caps:
                print(f"     • {cap}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
