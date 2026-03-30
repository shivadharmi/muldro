"""Composed MCP server — mounts intelligence + communication under one roof.

All internal Jarvis tools are accessible via this single FastMCP instance.
Use ``Client(jarvis_tools)`` for in-process MCP protocol calls (zero overhead).
Use ``jarvis_tools.run(transport="http")`` for multi-process deployments.
"""

from fastmcp import FastMCP
from fastmcp.contrib.component_manager import set_up_component_manager

from src.tools.communication_server import communication
from src.tools.intelligence_server import intelligence

jarvis_tools = FastMCP("jarvis-tools")

# Mount sub-servers with namespaces to avoid tool name collisions.
# Tools become: intelligence_search, communication_send_telegram, etc.
jarvis_tools.mount(intelligence, namespace="intelligence")
jarvis_tools.mount(communication, namespace="communication")

# Enable runtime tool enable/disable via Component Manager contrib.
set_up_component_manager(jarvis_tools)
