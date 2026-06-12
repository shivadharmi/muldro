"""Connector package — import all modules so @register_connector decorators fire."""

import src.connectors.calendar  # noqa: F401
import src.connectors.drive_connector  # noqa: F401
import src.connectors.github_connector  # noqa: F401
import src.connectors.gmail  # noqa: F401
import src.connectors.notion_connector  # noqa: F401
import src.connectors.slack_connector  # noqa: F401
import src.connectors.web_search_connector  # noqa: F401
import src.connectors.whatsapp_connector  # noqa: F401
