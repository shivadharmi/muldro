"""Google Sheets native adapter — read, write, append, create."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

SHEETS_API_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"


@dataclass(frozen=True, slots=True)
class SheetInfo:
    spreadsheet_id: str
    title: str
    sheet_names: list[str]
    url: str | None = None


@dataclass(frozen=True, slots=True)
class SheetRange:
    range: str
    values: list[list[str]]
    row_count: int = 0
    col_count: int = 0


class GoogleSheetsAdapter:
    """Direct Sheets API v4 adapter using OAuth access token."""

    def __init__(self, access_token: str):
        self._token = access_token
        self._headers = {"Authorization": f"Bearer {access_token}"}

    async def create(self, title: str, sheet_names: list[str] | None = None) -> SheetInfo:
        """Create a new spreadsheet."""
        body: dict = {"properties": {"title": title}}
        if sheet_names:
            body["sheets"] = [{"properties": {"title": name}} for name in sheet_names]

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                SHEETS_API_BASE,
                headers={**self._headers, "Content-Type": "application/json"},
                json=body,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

        return _parse_sheet_info(data)

    async def get(self, spreadsheet_id: str) -> SheetInfo:
        """Get spreadsheet metadata."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SHEETS_API_BASE}/{spreadsheet_id}",
                headers=self._headers,
                params={
                    "fields": "spreadsheetId,properties.title,sheets.properties.title,"
                    "spreadsheetUrl"
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            return _parse_sheet_info(resp.json())

    async def read_range(
        self,
        spreadsheet_id: str,
        range_notation: str,
        value_render: str = "FORMATTED_VALUE",
    ) -> SheetRange:
        """Read values from a range (e.g., 'Sheet1!A1:D10')."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{range_notation}",
                headers=self._headers,
                params={"valueRenderOption": value_render},
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

        values = data.get("values", [])
        return SheetRange(
            range=data.get("range", range_notation),
            values=values,
            row_count=len(values),
            col_count=max((len(row) for row in values), default=0),
        )

    async def write_range(
        self,
        spreadsheet_id: str,
        range_notation: str,
        values: list[list[str]],
    ) -> int:
        """Write values to a range. Returns number of updated cells."""
        body = {
            "range": range_notation,
            "majorDimension": "ROWS",
            "values": values,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{range_notation}",
                headers={**self._headers, "Content-Type": "application/json"},
                params={"valueInputOption": "USER_ENTERED"},
                json=body,
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json().get("updatedCells", 0)

    async def append_rows(
        self,
        spreadsheet_id: str,
        range_notation: str,
        values: list[list[str]],
    ) -> int:
        """Append rows after the last row with data."""
        body = {"majorDimension": "ROWS", "values": values}

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{range_notation}:append",
                headers={**self._headers, "Content-Type": "application/json"},
                params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                json=body,
                timeout=30.0,
            )
            resp.raise_for_status()
            updates = resp.json().get("updates", {})
            return updates.get("updatedRows", 0)

    async def clear_range(self, spreadsheet_id: str, range_notation: str) -> None:
        """Clear values from a range."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SHEETS_API_BASE}/{spreadsheet_id}/values/{range_notation}:clear",
                headers={**self._headers, "Content-Type": "application/json"},
                json={},
                timeout=30.0,
            )
            resp.raise_for_status()

    async def add_sheet(self, spreadsheet_id: str, title: str) -> int:
        """Add a new sheet tab. Returns the new sheet ID."""
        body = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{SHEETS_API_BASE}/{spreadsheet_id}:batchUpdate",
                headers={**self._headers, "Content-Type": "application/json"},
                json=body,
                timeout=30.0,
            )
            resp.raise_for_status()
            replies = resp.json().get("replies", [])
            if replies:
                return replies[0].get("addSheet", {}).get("properties", {}).get("sheetId", 0)
            return 0

    async def delete(self, spreadsheet_id: str) -> None:
        """Delete a spreadsheet (moves to trash via Drive API)."""
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{DRIVE_API_BASE}/files/{spreadsheet_id}",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"trashed": True},
                timeout=30.0,
            )
            resp.raise_for_status()


def _parse_sheet_info(data: dict) -> SheetInfo:
    sheets = data.get("sheets", [])
    sheet_names = [s.get("properties", {}).get("title", "") for s in sheets]
    return SheetInfo(
        spreadsheet_id=data.get("spreadsheetId", ""),
        title=data.get("properties", {}).get("title", ""),
        sheet_names=sheet_names,
        url=data.get("spreadsheetUrl"),
    )
