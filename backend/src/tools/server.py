"""Composed MCP server — mounts intelligence + communication under one roof.

All internal Muldro tools are accessible via this single FastMCP instance.
Use ``Client(muldro_tools)`` for in-process MCP protocol calls (zero overhead).
Use ``muldro_tools.run(transport="http")`` for multi-process deployments.
"""

from fastmcp import FastMCP
from fastmcp.contrib.component_manager import set_up_component_manager

from src.tools.communication_server import communication
from src.tools.intelligence_server import intelligence

muldro_tools = FastMCP("muldro-tools")

# Mount sub-servers with namespaces to avoid tool name collisions.
# Tools become: intelligence_search, communication_push_ui_update, etc.
muldro_tools.mount(intelligence, namespace="intelligence")
muldro_tools.mount(communication, namespace="communication")

# Enable runtime tool enable/disable via Component Manager contrib.
set_up_component_manager(muldro_tools)
