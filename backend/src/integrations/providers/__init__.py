"""Native provider adapters for direct API access.

Each adapter wraps a third-party API (Google Drive, Docs, Sheets, GitHub App)
with a consistent interface: list, get, create, update, delete.
All adapters accept OAuth credentials and return normalized dicts.
"""
