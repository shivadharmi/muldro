"""Tests for workspace surface cap with priority-weighted eviction."""

from unittest.mock import MagicMock


class TestSurfaceCap:
    def test_under_cap_unchanged(self):
        from src.services.surface_mapping import apply_surface_cap

        surfaces = []
        for i in range(10):
            mock = MagicMock()
            mock.kind = "summary"
            mock.created_at = f"2026-04-13T10:{i:02d}:00Z"
            surfaces.append(mock)

        result = apply_surface_cap(surfaces)
        assert len(result) == 10

    def test_cap_truncates_to_20(self):
        from src.services.surface_mapping import apply_surface_cap

        surfaces = []
        for i in range(25):
            mock = MagicMock()
            mock.kind = "summary"
            mock.created_at = f"2026-04-13T{10 + i // 60:02d}:{i % 60:02d}:00Z"
            surfaces.append(mock)

        result = apply_surface_cap(surfaces)
        assert len(result) == 20

    def test_approvals_never_evicted(self):
        from src.services.surface_mapping import apply_surface_cap

        surfaces = []
        for i in range(15):
            mock = MagicMock()
            mock.kind = "approval"
            mock.created_at = f"2026-04-13T10:{i:02d}:00Z"
            surfaces.append(mock)
        for i in range(10):
            mock = MagicMock()
            mock.kind = "summary"
            mock.created_at = f"2026-04-13T11:{i:02d}:00Z"
            surfaces.append(mock)

        result = apply_surface_cap(surfaces)
        assert len(result) == 20
        approval_count = sum(1 for s in result if s.kind == "approval")
        assert approval_count == 15

    def test_higher_priority_survives(self):
        from src.services.surface_mapping import apply_surface_cap

        surfaces = []
        for i in range(10):
            mock = MagicMock()
            mock.kind = "summary"
            mock.created_at = f"2026-04-13T10:{i:02d}:00Z"
            surfaces.append(mock)
        for i in range(10):
            mock = MagicMock()
            mock.kind = "plan"
            mock.created_at = f"2026-04-13T11:{i:02d}:00Z"
            surfaces.append(mock)
        for i in range(5):
            mock = MagicMock()
            mock.kind = "alert"
            mock.created_at = f"2026-04-13T12:{i:02d}:00Z"
            surfaces.append(mock)

        result = apply_surface_cap(surfaces)
        assert len(result) == 20
        plan_count = sum(1 for s in result if s.kind == "plan")
        alert_count = sum(1 for s in result if s.kind == "alert")
        assert plan_count == 10
        assert alert_count == 5

    def test_empty_list(self):
        from src.services.surface_mapping import apply_surface_cap

        result = apply_surface_cap([])
        assert result == []

    def test_newest_within_tier_survives(self):
        from src.services.surface_mapping import apply_surface_cap

        surfaces = []
        for i in range(25):
            mock = MagicMock()
            mock.kind = "summary"
            mock.created_at = f"2026-04-13T10:{i:02d}:00Z"
            surfaces.append(mock)

        result = apply_surface_cap(surfaces)
        assert len(result) == 20
        # Newest should survive — created_at "10:24" should be in result, "10:00" should not
        created_ats = [s.created_at for s in result]
        assert "2026-04-13T10:24:00Z" in created_ats
        assert "2026-04-13T10:00:00Z" not in created_ats
