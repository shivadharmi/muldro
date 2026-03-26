"""Tests for org controls service."""

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USER_ID, TEST_WORKSPACE_ID


class TestOrgAllowlist:
    async def test_list_allowlist_empty(self):
        from src.integrations.org_controls import OrgControlService

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        svc = OrgControlService(db, TEST_WORKSPACE_ID)
        entries = await svc.list_allowlist()
        assert entries == []

    async def test_add_to_allowlist(self):
        from src.integrations.org_controls import OrgControlService

        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        svc = OrgControlService(db, TEST_WORKSPACE_ID)
        entry = await svc.add_to_allowlist(
            server_name="test-server",
            added_by=TEST_USER_ID,
            max_trust_tier="T2",
            reason="Approved by admin",
        )

        assert entry.server_name == "test-server"
        assert entry.max_trust_tier == "T2"
        assert entry.requires_approval is True
        db.add.assert_called_once()

    async def test_remove_from_allowlist_not_found(self):
        from src.integrations.org_controls import OrgControlService

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        svc = OrgControlService(db, TEST_WORKSPACE_ID)
        ok = await svc.remove_from_allowlist("oal_nonexistent")
        assert ok is False

    async def test_is_allowed_no_allowlist(self):
        from src.integrations.org_controls import OrgControlService

        db = AsyncMock()
        db.scalar = AsyncMock(return_value=None)

        svc = OrgControlService(db, TEST_WORKSPACE_ID)
        assert await svc.is_allowed("any-server") is True


class TestOrgCatalog:
    async def test_list_catalog_empty(self):
        from src.integrations.org_controls import OrgControlService

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        svc = OrgControlService(db, TEST_WORKSPACE_ID)
        entries = await svc.list_catalog()
        assert entries == []

    async def test_get_catalog_entry_not_found(self):
        from src.integrations.org_controls import OrgControlService

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        svc = OrgControlService(db, TEST_WORKSPACE_ID)
        entry = await svc.get_catalog_entry("mcat_nonexistent")
        assert entry is None

    async def test_deprecate_catalog_entry_not_found(self):
        from src.integrations.org_controls import OrgControlService

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        svc = OrgControlService(db, TEST_WORKSPACE_ID)
        ok = await svc.deprecate_catalog_entry("mcat_nonexistent")
        assert ok is False
