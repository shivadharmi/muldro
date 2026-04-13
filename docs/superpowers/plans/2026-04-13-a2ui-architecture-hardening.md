# A2UI Architecture Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 14 identified issues in the A2UI system: dead code, type safety, rate limiting, surface cap, REST/WS unification, Presenter-driven surfaces, and complete detail tabs.

**Architecture:** 4-phase layered refactor. Phase 1 cleans the foundation (dead code, type unification, boundary fixes). Phase 2 adds typed properties, converges REST/WS delivery, adds rate limiting and surface cap. Phase 3 moves surface kind decisions from hardcoded heuristics to the Presenter agent. Phase 4 adds detail tabs for all 12 surface kinds.

**Tech Stack:** Python 3.12, Pydantic v2, FastAPI, SQLAlchemy async, Redis, React/Next.js, Zustand, TypeScript

**Spec:** `docs/superpowers/specs/2026-04-13-a2ui-architecture-hardening-design.md`

---

## File Map

### Phase 1 — Clean Foundation
| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `backend/src/ui/contracts.py` | Remove IMAGE, COMMAND_PALETTE from ComponentType enum |
| Modify | `backend/src/ui/renderer.py` | Delete `briefing_surface()`, `surface()`, remove A2UISurface import |
| Modify | `backend/src/orchestrator/contracts.py` | Import SurfaceKind from ui.contracts, use in WorkspaceSurfacePush |
| Modify | `backend/src/orchestrator/jarvis.py` | Import from surface_mapping, remove local `_derive_surface_kind` / `_build_surface_preview_from_plan` |
| Create | `backend/src/services/surface_mapping.py` | Relocated surface kind derivation + preview builder |
| Modify | `backend/tests/test_orchestrator.py` | Delete `test_briefing_surface`, update `test_surface_serialization` |
| Modify | `frontend/src/stores/surface-store.ts` | Remove `children?` field |
| Modify | `frontend/src/components/a2ui/renderer.tsx` | Remove ExecutionSurface case |

### Phase 2 — Typed Contracts + Unified Delivery
| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/src/ui/component_properties.py` | 22 property models + PROPERTY_MODELS registry |
| Modify | `backend/src/ui/contracts.py` | Add model_validator to A2UIComponent |
| Modify | `backend/src/ui/renderer.py` | All 36 builders use property models |
| Modify | `backend/src/orchestrator/contracts.py` | Extend WorkspaceSurfacePush with REST-only fields |
| Modify | `backend/src/services/surface_builder.py` | Return `list[WorkspaceSurfacePush]`, add cap/eviction |
| Modify | `backend/src/services/surface_mapping.py` | Add PRIORITY_TIERS constant |
| Modify | `backend/src/orchestrator/jarvis.py` | Add `_check_surface_rate()` |
| Modify | `backend/src/api/routes_ui.py` | Update response model |
| Modify | `frontend/src/stores/surface-store.ts` | Add cap enforcement |
| Modify | `frontend/src/app/page.tsx` | Remove `as SurfaceKind` casts, use typed response |
| Modify | `frontend/src/app/chat/page.tsx` | Remove `as SurfaceKind` casts |
| Create | `backend/tests/test_component_properties.py` | Property model validation tests |
| Create | `backend/tests/test_surface_rate_limit.py` | Rate limiting + cap tests |

### Phase 3 — Presenter-Driven Surface Architecture
| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `backend/src/orchestrator/contracts.py` | Add SurfaceSpec model |
| Modify | `backend/src/orchestrator/prompts.py` | Add SURFACE_GENERATION to PRESENTER_PROMPT |
| Modify | `backend/src/orchestrator/jarvis.py` | Replace push calls with `_push_presenter_surface`, add parser integration |
| Modify | `backend/src/services/surface_mapping.py` | Add `_extract_surface_spec()`, `_extract_surface_data()`, delete old functions |
| Create | `backend/tests/test_surface_spec.py` | SurfaceSpec parsing + validation tests |

### Phase 4 — Detail Tabs + Enrichment
| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `backend/src/ui/renderer.py` | Update `_TABS_BY_KIND` to cover all 12 kinds |
| Modify | `backend/src/services/surface_detail_builders.py` | Add 14 new builders, modify 1 |
| Modify | `backend/src/api/routes_surface_detail.py` | Add `surf_` prefix handling |
| Create | `backend/tests/test_detail_tabs_new.py` | Tests for all new tab builders |

---

## Phase 1: Clean Foundation

### Task 1: Remove dead ComponentType entries and fix SurfaceKind duplication

**Files:**
- Modify: `backend/src/ui/contracts.py:41-78` (ComponentType enum)
- Modify: `backend/src/orchestrator/contracts.py:221-256` (WorkspaceSurfacePush)
- Test: `backend/tests/test_orchestrator.py` (existing tests)

- [ ] **Step 1: Remove IMAGE and COMMAND_PALETTE from ComponentType enum**

In `backend/src/ui/contracts.py`, remove the two dead entries from the `ComponentType` enum:

```python
# Remove these two lines from the enum:
    IMAGE = "Image"
    COMMAND_PALETTE = "CommandPalette"
```

The enum should go from `ENTITY_CARD`, `MEMORY_CARD` directly to `EXECUTION_TRACE`:

```python
    ENTITY_CARD = "EntityCard"
    MEMORY_CARD = "MemoryCard"
    # Specialized
    EXECUTION_TRACE = "ExecutionTrace"
    KANBAN_BOARD = "KanbanBoard"
    CALENDAR = "Calendar"
```

- [ ] **Step 2: Fix SurfaceKind duplication in WorkspaceSurfacePush**

In `backend/src/orchestrator/contracts.py`, replace the inline `Literal` for `kind` with an import of `SurfaceKind`:

Add to the imports at the top of the file:

```python
from src.ui.contracts import SurfaceKind
```

Then change `WorkspaceSurfacePush.kind` from the inline literal to:

```python
class WorkspaceSurfacePush(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["surface"] = "surface"
    id: str
    kind: SurfaceKind
    preview: Any
    detail_config: Any | None = None
    decision: str | None = None
    source_run_id: str | None = None
    response_preview: str | None = None
    created_at: str = ""
    ttl_hours: int = 24
```

- [ ] **Step 3: Run tests to verify nothing breaks**

Run: `cd backend && python -m pytest tests/test_orchestrator.py -v -x`
Expected: All tests pass. The enum entries were unused, and the SurfaceKind values are identical.

- [ ] **Step 4: Run ruff**

Run: `cd backend && ruff check src/ui/contracts.py src/orchestrator/contracts.py`
Expected: No errors.

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/ui/contracts.py src/orchestrator/contracts.py
git commit -m "refactor: remove dead ComponentType entries, deduplicate SurfaceKind"
```

---

### Task 2: Delete dead `briefing_surface()` and `surface()` from renderer

**Files:**
- Modify: `backend/src/ui/renderer.py:7-13` (imports), `383-543` (functions)
- Modify: `backend/tests/test_orchestrator.py:495-524` (tests)

- [ ] **Step 1: Remove A2UISurface from renderer imports**

In `backend/src/ui/renderer.py`, change the imports from:

```python
from src.ui.contracts import (
    A2UIAction,
    A2UIComponent,
    A2UISurface,
    DetailConfig,
    DetailTab,
)
```

To:

```python
from src.ui.contracts import (
    A2UIAction,
    A2UIComponent,
    DetailConfig,
    DetailTab,
)
```

- [ ] **Step 2: Delete `surface()` helper function**

Delete `backend/src/ui/renderer.py` lines 383-388:

```python
def surface(
    id: str,
    children: list[A2UIComponent],
    metadata: dict | None = None,
) -> A2UISurface:
    return A2UISurface(id=id, children=children, metadata=metadata or {})
```

- [ ] **Step 3: Delete `briefing_surface()` function**

Delete `backend/src/ui/renderer.py` lines 420-543 (the entire `briefing_surface` function and its `_TABS_BY_KIND` stays — it's above `briefing_surface`).

Note: `build_detail_config()` (line 404-414) and `_TABS_BY_KIND` (line 394-401) stay — they are actively used by `surface_builder.py` and `surface_detail_builders.py`.

- [ ] **Step 4: Delete tests for removed functions**

In `backend/tests/test_orchestrator.py`, delete `test_briefing_surface` (lines 495-514) and `test_surface_serialization` (lines 516-524). Both test functions that no longer exist.

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_orchestrator.py -v -x`
Expected: All remaining tests pass (the deleted tests are gone).

- [ ] **Step 6: Run ruff**

Run: `cd backend && ruff check src/ui/renderer.py`
Expected: No errors.

- [ ] **Step 7: Commit**

```bash
cd backend && git add src/ui/renderer.py tests/test_orchestrator.py
git commit -m "refactor: delete dead briefing_surface() and surface() from renderer"
```

---

### Task 3: Move `_derive_surface_kind` and `_build_surface_preview_from_plan` to surface_mapping.py

**Files:**
- Create: `backend/src/services/surface_mapping.py`
- Modify: `backend/src/orchestrator/jarvis.py:90-159` (delete functions), `976`, `1313`, `2027` (update call sites)

- [ ] **Step 1: Create `surface_mapping.py` with the relocated functions**

Create `backend/src/services/surface_mapping.py`:

```python
"""Surface kind derivation and preview building for WS surface pushes.

These functions map PlanOutput capabilities to surface kinds and build
SurfacePreview data for workspace grid cards. Phase 1 relocates them
from jarvis.py; Phase 3 replaces them with Presenter-driven SurfaceSpec.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.orchestrator.contracts import PlanOutput


def derive_surface_kind(plan: PlanOutput) -> tuple[str, str] | None:
    """Derive workspace surface kind from PlanOutput step capabilities.

    Returns (kind, default_title) or None if the plan is chat-only.
    """
    if not plan.steps:
        return None

    caps = {s.capability for s in plan.steps if s.actor == "jarvis"}

    if caps <= {"reason", "respond", "none"}:
        return None

    if "system.add_to_brief" in caps:
        return ("briefing", "Briefing Update")
    if "system.schedule_reminder" in caps:
        return ("alert", "Reminder Scheduled")

    if any(s.risk in ("medium", "high") for s in plan.steps):
        return ("plan", "New Plan")

    jarvis_steps = [s for s in plan.steps if s.actor == "jarvis"]
    if len(jarvis_steps) > 2:
        return ("plan", plan.goal[:80] or "Plan")

    return ("summary", "Summary")


def build_surface_preview_from_plan(
    plan: PlanOutput,
    kind: str,
    default_title: str,
    response_text: str,
):
    """Build a SurfacePreview from a PlanOutput for workspace grid cards."""
    from src.ui.contracts import SurfaceMetric, SurfacePreview

    title = plan.goal[:80] if plan.goal else default_title
    subtitle = plan.reasoning[:120] if plan.reasoning else None
    metrics: list[SurfaceMetric] = []
    tags: list[str] = []

    if kind == "plan":
        step_count = len([s for s in plan.steps if s.actor == "jarvis"])
        if step_count:
            metrics.append(SurfaceMetric(label="Steps", value=str(step_count)))
        metrics.append(SurfaceMetric(label="Priority", value=plan.priority))
    elif kind == "summary":
        tags.append("read")
    elif kind == "briefing":
        tags.append("briefing")
    elif kind == "alert":
        tags.append("reminder")

    return SurfacePreview(
        title=title,
        subtitle=subtitle,
        status=None,
        priority=plan.priority if plan.priority != "medium" else None,
        metrics=metrics,
        entities=[],
        progress=None,
        tags=tags,
    )
```

- [ ] **Step 2: Update jarvis.py — delete local functions, import from surface_mapping**

In `backend/src/orchestrator/jarvis.py`:

Delete lines 90-159 (the `_derive_surface_kind` and `_build_surface_preview_from_plan` functions).

Add an import near the top of the file (after the existing imports, before the class):

```python
from src.services.surface_mapping import derive_surface_kind, build_surface_preview_from_plan
```

- [ ] **Step 3: Update the 3 call sites in jarvis.py**

The function names changed from `_derive_surface_kind` to `derive_surface_kind` and `_build_surface_preview_from_plan` to `build_surface_preview_from_plan` (removed underscore prefix since they're now public module functions).

In `_push_workspace_surface` (around line 2140 after deletion offset):

Change:
```python
mapping = _derive_surface_kind(plan)
```
To:
```python
mapping = derive_surface_kind(plan)
```

Change:
```python
preview = _build_surface_preview_from_plan(plan, kind, default_title, response_text)
```
To:
```python
preview = build_surface_preview_from_plan(plan, kind, default_title, response_text)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && python -m pytest tests/test_orchestrator.py -v -x`
Expected: All tests pass. Pure relocation — no logic change.

- [ ] **Step 5: Run ruff on both files**

Run: `cd backend && ruff check src/orchestrator/jarvis.py src/services/surface_mapping.py`
Expected: No errors.

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/services/surface_mapping.py src/orchestrator/jarvis.py
git commit -m "refactor: move surface kind derivation from jarvis.py to surface_mapping.py"
```

---

### Task 4: Frontend cleanup — remove children field and ExecutionSurface phantom

**Files:**
- Modify: `frontend/src/stores/surface-store.ts:18`
- Modify: `frontend/src/components/a2ui/renderer.tsx:11,146`

- [ ] **Step 1: Remove `children?` from WorkspaceSurface**

In `frontend/src/stores/surface-store.ts`, remove the `children` field and its import. Change:

```typescript
import type { A2UIComponent } from "@/lib/a2ui-types";
```

Remove that import line entirely (only if `A2UIComponent` is not used elsewhere in the file — check that it's only used for the `children` field).

Remove from the `WorkspaceSurface` interface:

```typescript
  children?: A2UIComponent[];
```

- [ ] **Step 2: Remove ExecutionSurface case from renderer**

In `frontend/src/components/a2ui/renderer.tsx`:

Remove the import:
```typescript
import { A2UIExecutionSurface } from "./components/execution-surface";
```

Remove the case from `renderComponentInner`:
```typescript
    case "ExecutionSurface":
      return <A2UIExecutionSurface key={component.id} component={component} />;
```

- [ ] **Step 3: Verify frontend builds**

Run: `cd frontend && npm run build`
Expected: Build succeeds. No component references `children` on `WorkspaceSurface`, and no backend code emits `type: "ExecutionSurface"`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/stores/surface-store.ts frontend/src/components/a2ui/renderer.tsx
git commit -m "refactor: remove vestigial children field and phantom ExecutionSurface type"
```

---

## Phase 2: Typed Contracts + Unified Delivery

### Task 5: Create typed component property models

**Files:**
- Create: `backend/src/ui/component_properties.py`
- Test: `backend/tests/test_component_properties.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_component_properties.py`:

```python
"""Tests for typed A2UI component property models."""

import pytest
from pydantic import ValidationError


class TestTextProperties:
    def test_valid_text(self):
        from src.ui.component_properties import TextProperties

        p = TextProperties(text="Hello", variant="body")
        assert p.text == "Hello"
        assert p.variant == "body"

    def test_heading_variant(self):
        from src.ui.component_properties import TextProperties

        p = TextProperties(text="Title", variant="heading")
        assert p.variant == "heading"

    def test_invalid_variant_rejected(self):
        from src.ui.component_properties import TextProperties

        with pytest.raises(ValidationError):
            TextProperties(text="Hello", variant="invalid")

    def test_missing_text_rejected(self):
        from src.ui.component_properties import TextProperties

        with pytest.raises(ValidationError):
            TextProperties(variant="body")


class TestButtonProperties:
    def test_valid_button(self):
        from src.ui.component_properties import ButtonProperties

        p = ButtonProperties(label="Click me", variant="primary")
        assert p.label == "Click me"

    def test_all_variants_accepted(self):
        from src.ui.component_properties import ButtonProperties

        for v in ("primary", "secondary", "danger", "ghost"):
            p = ButtonProperties(label="Test", variant=v)
            assert p.variant == v

    def test_invalid_variant_rejected(self):
        from src.ui.component_properties import ButtonProperties

        with pytest.raises(ValidationError):
            ButtonProperties(label="Test", variant="neon")


class TestBadgeProperties:
    def test_valid_badge(self):
        from src.ui.component_properties import BadgeProperties

        p = BadgeProperties(label="Active", variant="success")
        assert p.label == "Active"
        assert p.variant == "success"

    def test_default_variant(self):
        from src.ui.component_properties import BadgeProperties

        p = BadgeProperties(label="Tag")
        assert p.variant == "default"


class TestTableProperties:
    def test_valid_table(self):
        from src.ui.component_properties import TableProperties

        p = TableProperties(
            columns=[{"key": "name", "label": "Name"}],
            rows=[{"name": "Alice"}],
        )
        assert len(p.columns) == 1
        assert p.sortable is False


class TestMetricProperties:
    def test_string_value(self):
        from src.ui.component_properties import MetricProperties

        p = MetricProperties(label="Users", value="1,234")
        assert p.value == "1,234"

    def test_numeric_value(self):
        from src.ui.component_properties import MetricProperties

        p = MetricProperties(label="Revenue", value=42.5, change="+5%", trend="up")
        assert p.value == 42.5
        assert p.trend == "up"


class TestPropertyModelsRegistry:
    def test_registry_has_all_22_entries(self):
        from src.ui.component_properties import PROPERTY_MODELS

        assert len(PROPERTY_MODELS) == 22

    def test_layout_containers_not_in_registry(self):
        from src.ui.component_properties import PROPERTY_MODELS

        for name in ("Card", "Row", "Column", "List", "Divider", "Form"):
            assert name not in PROPERTY_MODELS

    def test_all_entries_are_basemodel_subclasses(self):
        from pydantic import BaseModel

        from src.ui.component_properties import PROPERTY_MODELS

        for name, model in PROPERTY_MODELS.items():
            assert issubclass(model, BaseModel), f"{name} is not a BaseModel"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_component_properties.py -v -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.ui.component_properties'`

- [ ] **Step 3: Create the component_properties module**

Create `backend/src/ui/component_properties.py`:

```python
"""Typed property models for A2UI components.

Each model validates the properties dict for a specific component type.
Layout containers (Card, Row, Column, List, Divider, Form) use empty
properties and are NOT included — they have no required fields.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


# ── Text family ──────────────────────────────────────────────────


class TextProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str
    variant: Literal["heading", "body", "caption"] = "body"


class CodeBlockProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    language: str = "text"


class BadgeProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    variant: Literal["default", "success", "warning", "danger"] = "default"


class AlertProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str
    severity: Literal["info", "warning", "error", "success"] = "info"
    title: str | None = None


# ── Input family ─────────────────────────────────────────────────


class ButtonProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    variant: Literal["primary", "secondary", "danger", "ghost"] = "primary"


class TextFieldProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str = ""
    placeholder: str = ""
    value: str = ""


class SelectProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    options: list[dict] = []
    value: str = ""


class ToggleProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    checked: bool = False


# ── Data family ──────────────────────────────────────────────────


class TableProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    columns: list[dict]
    rows: list[dict]
    sortable: bool = False


class DataGridProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    columns: list[dict]
    rows: list[dict]
    page_size: int = 20


class TimelineProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[dict]


class MetricProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    label: str
    value: str | int | float
    change: str | None = None
    trend: str | None = None


class ProgressProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: float
    max: float = 100
    label: str | None = None


class ChartProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    chart_type: str
    data: dict
    title: str = ""


# ── Display family ───────────────────────────────────────────────


class AvatarProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    url: str | None = None
    size: Literal["sm", "md", "lg"] = "md"


class StatusIndicatorProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    label: str = ""


class EntityCardProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    entity_type: str
    entity_id: str = ""
    attributes: dict | None = None


class MemoryCardProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    fact_text: str
    memory_type: str
    source: str = ""
    confidence: float = 1.0


# ── Specialized family ───────────────────────────────────────────


class ExecutionTraceProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    steps: list[dict]
    status: str = "running"


class KanbanBoardProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    columns: list[dict]


class CalendarProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    events: list[dict]
    view: Literal["day", "week", "month"] = "week"


# ── Layout with properties ───────────────────────────────────────


class TabsProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    active_tab: int = 0
    labels: list[str]


class ModalProperties(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str
    open: bool = True


# ── Registry ─────────────────────────────────────────────────────

PROPERTY_MODELS: dict[str, type[BaseModel]] = {
    "Text": TextProperties,
    "CodeBlock": CodeBlockProperties,
    "Badge": BadgeProperties,
    "Alert": AlertProperties,
    "Button": ButtonProperties,
    "TextField": TextFieldProperties,
    "Select": SelectProperties,
    "Toggle": ToggleProperties,
    "Table": TableProperties,
    "DataGrid": DataGridProperties,
    "Timeline": TimelineProperties,
    "Metric": MetricProperties,
    "Progress": ProgressProperties,
    "Chart": ChartProperties,
    "Avatar": AvatarProperties,
    "StatusIndicator": StatusIndicatorProperties,
    "EntityCard": EntityCardProperties,
    "MemoryCard": MemoryCardProperties,
    "ExecutionTrace": ExecutionTraceProperties,
    "KanbanBoard": KanbanBoardProperties,
    "Calendar": CalendarProperties,
    "Tabs": TabsProperties,
    "Modal": ModalProperties,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_component_properties.py -v`
Expected: All 12 tests pass.

- [ ] **Step 5: Commit**

```bash
cd backend && git add src/ui/component_properties.py tests/test_component_properties.py
git commit -m "feat: add typed property models for all 22 A2UI component types"
```

---

### Task 6: Wire property validation into A2UIComponent

**Files:**
- Modify: `backend/src/ui/contracts.py:87-109` (A2UIComponent class)
- Test: `backend/tests/test_component_properties.py` (add validation tests)

- [ ] **Step 1: Write the failing tests for validation**

Append to `backend/tests/test_component_properties.py`:

```python
class TestA2UIComponentValidation:
    def test_valid_button_passes_validation(self):
        from src.ui.contracts import A2UIComponent

        c = A2UIComponent(
            type="Button", id="btn1", properties={"label": "Click", "variant": "primary"}
        )
        assert c.type == "Button"

    def test_button_missing_label_rejected(self):
        from src.ui.contracts import A2UIComponent

        with pytest.raises(ValidationError):
            A2UIComponent(type="Button", id="btn1", properties={"variant": "primary"})

    def test_button_invalid_variant_rejected(self):
        from src.ui.contracts import A2UIComponent

        with pytest.raises(ValidationError):
            A2UIComponent(type="Button", id="btn1", properties={"label": "OK", "variant": "neon"})

    def test_text_valid(self):
        from src.ui.contracts import A2UIComponent

        c = A2UIComponent(type="Text", id="t1", properties={"text": "Hello"})
        assert c.properties["text"] == "Hello"

    def test_layout_container_skips_validation(self):
        from src.ui.contracts import A2UIComponent

        c = A2UIComponent(type="Card", id="c1", properties={})
        assert c.type == "Card"

    def test_unknown_type_skips_validation(self):
        from src.ui.contracts import A2UIComponent

        c = A2UIComponent(type="FutureWidget", id="fw1", properties={"anything": True})
        assert c.type == "FutureWidget"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_component_properties.py::TestA2UIComponentValidation -v -x`
Expected: FAIL — `test_button_missing_label_rejected` passes (no validation) when it should raise. The tests that expect ValidationError will fail because no validation exists yet.

- [ ] **Step 3: Add model_validator to A2UIComponent**

In `backend/src/ui/contracts.py`, add the import and validator to the `A2UIComponent` class:

Add to imports at top:

```python
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
```

(Add `model_validator` to the existing import.)

Then add the validator method inside the `A2UIComponent` class, after the existing `validate_id_not_empty` method:

```python
    @model_validator(mode="after")
    def _validate_properties(self) -> "A2UIComponent":
        from src.ui.component_properties import PROPERTY_MODELS

        model = PROPERTY_MODELS.get(self.type)
        if model is not None:
            model(**self.properties)
        return self
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_component_properties.py -v`
Expected: All 18 tests pass (12 original + 6 new).

- [ ] **Step 5: Run all orchestrator tests to check nothing breaks**

Run: `cd backend && python -m pytest tests/test_orchestrator.py -v -x`
Expected: All pass. Existing code already uses builder functions which produce correct properties.

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/ui/contracts.py tests/test_component_properties.py
git commit -m "feat: wire property validation into A2UIComponent model_validator"
```

---

### Task 7: Update renderer builder functions to use property models

**Files:**
- Modify: `backend/src/ui/renderer.py` (all 36 builder functions)

- [ ] **Step 1: Update imports in renderer.py**

Add to the top of `backend/src/ui/renderer.py`:

```python
from src.ui.component_properties import (
    AlertProperties,
    AvatarProperties,
    BadgeProperties,
    ButtonProperties,
    CalendarProperties,
    ChartProperties,
    CodeBlockProperties,
    DataGridProperties,
    EntityCardProperties,
    ExecutionTraceProperties,
    KanbanBoardProperties,
    MemoryCardProperties,
    MetricProperties,
    ModalProperties,
    ProgressProperties,
    SelectProperties,
    StatusIndicatorProperties,
    TableProperties,
    TabsProperties,
    TextFieldProperties,
    TextProperties,
    TimelineProperties,
    ToggleProperties,
)
```

- [ ] **Step 2: Update text family builders**

Replace each builder to construct the property model, then dump to dict:

```python
def text(id: str, text: str, variant: str = "body") -> A2UIComponent:
    props = TextProperties(text=text, variant=variant)
    return A2UIComponent(type="Text", id=id, properties=props.model_dump())


def heading(id: str, text: str) -> A2UIComponent:
    props = TextProperties(text=text, variant="heading")
    return A2UIComponent(type="Text", id=id, properties=props.model_dump())


def caption(id: str, text: str) -> A2UIComponent:
    props = TextProperties(text=text, variant="caption")
    return A2UIComponent(type="Text", id=id, properties=props.model_dump())


def code_block(id: str, code: str, language: str = "text") -> A2UIComponent:
    props = CodeBlockProperties(code=code, language=language)
    return A2UIComponent(type="CodeBlock", id=id, properties=props.model_dump())


def badge(id: str, label: str, variant: str = "default") -> A2UIComponent:
    props = BadgeProperties(label=label, variant=variant)
    return A2UIComponent(type="Badge", id=id, properties=props.model_dump())


def alert(id: str, message: str, severity: str = "info", title: str | None = None) -> A2UIComponent:
    props = AlertProperties(message=message, severity=severity, title=title)
    return A2UIComponent(type="Alert", id=id, properties=props.model_dump())
```

- [ ] **Step 3: Update input family builders**

```python
def button(
    id: str,
    label: str,
    variant: str = "primary",
    action_payload: dict | None = None,
) -> A2UIComponent:
    props = ButtonProperties(label=label, variant=variant)
    actions = []
    if action_payload:
        actions = [A2UIAction(type="click", payload=action_payload)]
    return A2UIComponent(type="Button", id=id, properties=props.model_dump(), actions=actions)


def text_field(
    id: str,
    label: str = "",
    placeholder: str = "",
    value: str = "",
) -> A2UIComponent:
    props = TextFieldProperties(label=label, placeholder=placeholder, value=value)
    return A2UIComponent(type="TextField", id=id, properties=props.model_dump())


def select_field(
    id: str,
    label: str,
    options: list[dict],
    value: str = "",
) -> A2UIComponent:
    props = SelectProperties(label=label, options=options, value=value)
    return A2UIComponent(type="Select", id=id, properties=props.model_dump())


def toggle(
    id: str,
    label: str,
    checked: bool = False,
) -> A2UIComponent:
    props = ToggleProperties(label=label, checked=checked)
    return A2UIComponent(type="Toggle", id=id, properties=props.model_dump())


def form(
    id: str,
    fields: list[A2UIComponent],
    submit_label: str = "Submit",
    submit_payload: dict | None = None,
) -> A2UIComponent:
    submit = button(f"{id}_submit", submit_label, "primary", submit_payload)
    return A2UIComponent(type="Form", id=id, children=fields + [submit])
```

- [ ] **Step 4: Update data family builders**

```python
def table(
    id: str,
    columns: list[dict],
    rows: list[dict],
    sortable: bool = False,
) -> A2UIComponent:
    props = TableProperties(columns=columns, rows=rows, sortable=sortable)
    return A2UIComponent(type="Table", id=id, properties=props.model_dump())


def data_grid(
    id: str,
    columns: list[dict],
    rows: list[dict],
    page_size: int = 20,
) -> A2UIComponent:
    props = DataGridProperties(columns=columns, rows=rows, page_size=page_size)
    return A2UIComponent(type="DataGrid", id=id, properties=props.model_dump())


def timeline(id: str, events: list[dict]) -> A2UIComponent:
    props = TimelineProperties(events=events)
    return A2UIComponent(type="Timeline", id=id, properties=props.model_dump())


def metric(
    id: str,
    label: str,
    value: str | int | float,
    change: str | None = None,
    trend: str | None = None,
) -> A2UIComponent:
    props = MetricProperties(label=label, value=value, change=change, trend=trend)
    return A2UIComponent(type="Metric", id=id, properties=props.model_dump())


def progress(
    id: str,
    value: float,
    max_value: float = 100,
    label: str | None = None,
) -> A2UIComponent:
    props = ProgressProperties(value=value, max=max_value, label=label)
    return A2UIComponent(type="Progress", id=id, properties=props.model_dump())


def chart(
    id: str,
    chart_type: str,
    data: dict,
    title: str = "",
) -> A2UIComponent:
    props = ChartProperties(chart_type=chart_type, data=data, title=title)
    return A2UIComponent(type="Chart", id=id, properties=props.model_dump())
```

- [ ] **Step 5: Update display and specialized builders**

```python
def list_component(id: str, children: list[A2UIComponent]) -> A2UIComponent:
    return A2UIComponent(type="List", id=id, children=children)


def avatar(
    id: str,
    name: str,
    url: str | None = None,
    size: str = "md",
) -> A2UIComponent:
    props = AvatarProperties(name=name, url=url, size=size)
    return A2UIComponent(type="Avatar", id=id, properties=props.model_dump())


def status_indicator(
    id: str,
    status: str,
    label: str = "",
) -> A2UIComponent:
    props = StatusIndicatorProperties(status=status, label=label)
    return A2UIComponent(type="StatusIndicator", id=id, properties=props.model_dump())


def entity_card(
    id: str,
    name: str,
    entity_type: str,
    entity_id: str = "",
    attributes: dict | None = None,
) -> A2UIComponent:
    props = EntityCardProperties(
        name=name, entity_type=entity_type, entity_id=entity_id, attributes=attributes
    )
    return A2UIComponent(type="EntityCard", id=id, properties=props.model_dump())


def memory_card(
    id: str,
    fact_text: str,
    memory_type: str,
    source: str = "",
    confidence: float = 1.0,
) -> A2UIComponent:
    props = MemoryCardProperties(
        fact_text=fact_text, memory_type=memory_type, source=source, confidence=confidence
    )
    return A2UIComponent(type="MemoryCard", id=id, properties=props.model_dump())


def execution_trace(
    id: str,
    steps: list[dict],
    status: str = "running",
) -> A2UIComponent:
    props = ExecutionTraceProperties(steps=steps, status=status)
    return A2UIComponent(type="ExecutionTrace", id=id, properties=props.model_dump())


def kanban_board(
    id: str,
    columns_data: list[dict],
) -> A2UIComponent:
    props = KanbanBoardProperties(columns=columns_data)
    return A2UIComponent(type="KanbanBoard", id=id, properties=props.model_dump())


def calendar_view(
    id: str,
    events: list[dict],
    view: str = "week",
) -> A2UIComponent:
    props = CalendarProperties(events=events, view=view)
    return A2UIComponent(type="Calendar", id=id, properties=props.model_dump())
```

- [ ] **Step 6: Update layout builders with properties (tabs, modal)**

```python
def tabs(
    id: str,
    tab_labels: list[str],
    tab_contents: list[list[A2UIComponent]],
    active_tab: int = 0,
) -> A2UIComponent:
    props = TabsProperties(active_tab=active_tab, labels=tab_labels)
    children = []
    for i, (label, content) in enumerate(zip(tab_labels, tab_contents)):
        children.append(
            A2UIComponent(
                type="Card",
                id=f"{id}_tab_{i}",
                properties={"tab_label": label, "tab_index": i},
                children=content,
            )
        )
    return A2UIComponent(type="Tabs", id=id, properties=props.model_dump(), children=children)


def modal(
    id: str,
    title: str,
    children: list[A2UIComponent],
    open: bool = True,
) -> A2UIComponent:
    props = ModalProperties(title=title, open=open)
    return A2UIComponent(type="Modal", id=id, properties=props.model_dump(), children=children)
```

Note: The `Card` inside tabs has extra `tab_label`/`tab_index` properties that are not validated (Card is a layout container with no property model). This is intentional — these are rendering hints for the tabs component.

- [ ] **Step 7: Run all tests**

Run: `cd backend && python -m pytest tests/ -v -x -k "orchestrator or component_properties or surface"`
Expected: All pass. The builder functions produce the same output shape, now with validation.

- [ ] **Step 8: Run ruff**

Run: `cd backend && ruff check src/ui/renderer.py`
Expected: No errors.

- [ ] **Step 9: Commit**

```bash
cd backend && git add src/ui/renderer.py
git commit -m "refactor: update all 36 renderer builders to use typed property models"
```

---

### Task 8: Converge REST/WS on WorkspaceSurfacePush

**Files:**
- Modify: `backend/src/orchestrator/contracts.py` (extend WorkspaceSurfacePush)
- Modify: `backend/src/services/surface_builder.py` (return typed model)
- Modify: `backend/src/api/routes_ui.py` (update response model)
- Modify: `frontend/src/app/page.tsx` (remove casts)
- Modify: `frontend/src/app/chat/page.tsx` (remove casts)

- [ ] **Step 1: Extend WorkspaceSurfacePush with REST-only fields**

In `backend/src/orchestrator/contracts.py`, add fields to `WorkspaceSurfacePush`:

```python
class WorkspaceSurfacePush(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["surface"] = "surface"
    id: str
    kind: SurfaceKind
    preview: Any
    detail_config: Any | None = None
    decision: str | None = None
    source_run_id: str | None = None
    response_preview: str | None = None
    created_at: str = ""
    ttl_hours: int = 24
    # Merged from REST-only path
    trust_context: dict[str, str] | None = None
    insight_data: dict | None = None
    phase: str | None = None
    steps: list[dict] | None = None
    current_step: str | None = None
    progress: str | None = None
    approval: dict | None = None
    results: dict | None = None
```

- [ ] **Step 2: Update SurfaceService to return WorkspaceSurfacePush**

This is a large change to `backend/src/services/surface_builder.py`. Update the return type of `build_workspace_surfaces` and all `_build_*` methods from `list[dict[str, Any]]` to `list[WorkspaceSurfacePush]`.

Add import at top:
```python
from src.orchestrator.contracts import WorkspaceSurfacePush
```

Change `build_workspace_surfaces` signature:
```python
async def build_workspace_surfaces(self, user_id: str) -> list[WorkspaceSurfacePush]:
```

Change each `_build_*` method to construct `WorkspaceSurfacePush` instances instead of dicts. For example, `_build_approval_surfaces`:

```python
async def _build_approval_surfaces(self, user_id: str) -> list[WorkspaceSurfacePush]:
    # ... existing query logic unchanged ...
    surfaces: list[WorkspaceSurfacePush] = []

    for apr in approvals:
        surface_id = f"approval_{apr.approval_id}"
        # ... existing metric/preview logic unchanged ...

        surfaces.append(
            WorkspaceSurfacePush(
                id=surface_id,
                kind="approval",
                preview=preview.model_dump(mode="json"),
                detail_config=(
                    detail_config.model_dump(mode="json") if detail_config else None
                ),
                created_at=apr.created_at.isoformat() if apr.created_at else "",
                trust_context=trust_context,
            )
        )

    return surfaces
```

Apply the same pattern to all 7 `_build_*` methods. Each returns `list[WorkspaceSurfacePush]` (or `WorkspaceSurfacePush | None` for briefing). For `_build_briefing_surface`, wrap in a list or return directly.

For `_load_persisted_surfaces`, map the existing dict fields onto `WorkspaceSurfacePush` fields. The `last_surface_update` merge continues to work — just populate `phase`, `steps`, etc. directly on the model.

- [ ] **Step 3: Update REST endpoint response model**

In `backend/src/api/routes_ui.py`, change:

```python
from src.orchestrator.contracts import WorkspaceSurfacePush


class WorkspaceSurfacesResponse(BaseModel):
    surfaces: list[WorkspaceSurfacePush]
    count: int
```

The endpoint body stays the same — `SurfaceService` now returns the right type.

- [ ] **Step 4: Remove SurfaceKind casts in frontend**

In `frontend/src/app/page.tsx`, change the `restSurfaces` memo:

```typescript
  const restSurfaces = useMemo((): WorkspaceSurface[] => {
    const raw = workspaceData?.surfaces ?? [];
    return raw.map((s) => ({
      id: s.id,
      kind: s.kind || "summary",
      preview: s.preview,
      detail_config: s.detail_config,
      source_run_id: s.source_run_id ?? null,
      response_preview: s.response_preview ?? null,
      created_at: s.created_at ?? new Date().toISOString(),
      ...(s.phase && { phase: s.phase }),
      ...(s.steps && { steps: s.steps }),
      ...(s.current_step !== undefined && { current_step: s.current_step }),
      ...(s.progress && { progress: s.progress }),
      ...(s.approval && { approval: s.approval }),
      ...(s.results && { results: s.results }),
    }));
  }, [workspaceData]);
```

(Remove `as SurfaceKind` cast from `kind`)

Change `handleSurfacePush`:

```typescript
  const handleSurfacePush = useCallback(
    (push: WorkspaceSurfacePush) => {
      addSurface({
        id: push.id,
        kind: push.kind || "summary",
        preview: push.preview,
        detail_config: push.detail_config,
        source_run_id: push.source_run_id,
        response_preview: push.response_preview,
        created_at: push.created_at || new Date().toISOString(),
      });
    },
    [addSurface]
  );
```

(Remove `as SurfaceKind` cast)

In `frontend/src/app/chat/page.tsx`, apply same change to `handleSurfacePush`:

```typescript
  const handleSurfacePush = useCallback(
    (push: WorkspaceSurfacePush) => {
      addSurface({
        id: push.id,
        kind: push.kind || "summary",
        preview: push.preview,
        detail_config: push.detail_config,
        source_run_id: push.source_run_id,
        response_preview: push.response_preview,
        created_at: push.created_at || new Date().toISOString(),
      });
    },
    [addSurface]
  );
```

Also remove the `import type { SurfaceKind }` line from both files if it's no longer used.

- [ ] **Step 5: Run backend tests**

Run: `cd backend && python -m pytest tests/ -v -x -k "orchestrator or surface"`
Expected: All pass.

- [ ] **Step 6: Run frontend build**

Run: `cd frontend && npm run build`
Expected: Build succeeds.

- [ ] **Step 7: Commit**

```bash
git add backend/src/orchestrator/contracts.py backend/src/services/surface_builder.py backend/src/api/routes_ui.py frontend/src/app/page.tsx frontend/src/app/chat/page.tsx
git commit -m "feat: converge REST and WS surface delivery on WorkspaceSurfacePush"
```

---

### Task 9: Add surface push rate limiting

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (add `_check_surface_rate`, call in push functions)
- Test: `backend/tests/test_surface_rate_limit.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_surface_rate_limit.py`:

```python
"""Tests for surface push rate limiting and workspace cap."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSurfaceRateLimit:
    @pytest.mark.asyncio
    async def test_first_push_allowed(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        mock_settings = MagicMock()
        mock_settings.anthropic_api_key = "test"
        mock_settings.use_bedrock = False
        mock_settings.daily_token_budget_usd = 10.0

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        orch._event_bus = None

        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()

        mock_bus = MagicMock()
        mock_bus._redis = mock_redis
        orch._event_bus = mock_bus
        orch._ensure_event_bus = AsyncMock(return_value=mock_bus)

        result = await orch._check_surface_rate("user_123", "workspace")
        assert result is True
        mock_redis.incr.assert_called_once_with("jarvis:surface_rate:workspace:user_123")
        mock_redis.expire.assert_called_once_with("jarvis:surface_rate:workspace:user_123", 60)

    @pytest.mark.asyncio
    async def test_sixth_workspace_push_blocked(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)

        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=6)

        mock_bus = MagicMock()
        mock_bus._redis = mock_redis
        orch._event_bus = mock_bus
        orch._ensure_event_bus = AsyncMock(return_value=mock_bus)

        result = await orch._check_surface_rate("user_123", "workspace")
        assert result is False

    @pytest.mark.asyncio
    async def test_insight_rate_limit_window(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)

        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()

        mock_bus = MagicMock()
        mock_bus._redis = mock_redis
        orch._event_bus = mock_bus
        orch._ensure_event_bus = AsyncMock(return_value=mock_bus)

        result = await orch._check_surface_rate("user_123", "insight")
        assert result is True
        mock_redis.expire.assert_called_once_with(
            "jarvis:surface_rate:insight:user_123", 1800
        )

    @pytest.mark.asyncio
    async def test_fourth_insight_push_blocked(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)

        mock_redis = AsyncMock()
        mock_redis.incr = AsyncMock(return_value=4)

        mock_bus = MagicMock()
        mock_bus._redis = mock_redis
        orch._event_bus = mock_bus
        orch._ensure_event_bus = AsyncMock(return_value=mock_bus)

        result = await orch._check_surface_rate("user_123", "insight")
        assert result is False

    @pytest.mark.asyncio
    async def test_no_redis_allows_push(self):
        from src.orchestrator.jarvis import JarvisOrchestrator

        orch = JarvisOrchestrator.__new__(JarvisOrchestrator)
        orch._event_bus = None
        orch._ensure_event_bus = AsyncMock(return_value=None)

        result = await orch._check_surface_rate("user_123", "workspace")
        assert result is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_surface_rate_limit.py -v -x`
Expected: FAIL — `AttributeError: 'JarvisOrchestrator' object has no attribute '_check_surface_rate'`

- [ ] **Step 3: Add `_check_surface_rate` to JarvisOrchestrator**

In `backend/src/orchestrator/jarvis.py`, add this method to the `JarvisOrchestrator` class (near the other `_push_*` methods):

```python
    async def _check_surface_rate(self, user_id: str, surface_type: str) -> bool:
        """Return True if push is allowed under rate limit.

        Uses Redis INCR with TTL for a sliding window counter.
        Workspace: 5 per minute. Insight: 3 per 30 minutes.
        """
        event_bus = await self._ensure_event_bus()
        if not event_bus or not getattr(event_bus, "_redis", None):
            return True

        redis = event_bus._redis
        if surface_type == "insight":
            key = f"jarvis:surface_rate:insight:{user_id}"
            limit, window = 3, 1800
        else:
            key = f"jarvis:surface_rate:workspace:{user_id}"
            limit, window = 5, 60

        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)
        return count <= limit
```

- [ ] **Step 4: Call rate check in `_push_workspace_surface`**

At the top of `_push_workspace_surface` (after the `_derive_surface_kind` / `derive_surface_kind` check), add:

```python
        if not await self._check_surface_rate(user_id, "workspace"):
            logger.debug("Surface push rate-limited for user %s", user_id)
            return None
```

- [ ] **Step 5: Call rate check in `_push_insight_surface`**

At the top of `_push_insight_surface` (after getting event_bus), add:

```python
        if not await self._check_surface_rate(user_id, "insight"):
            logger.debug("Insight surface rate-limited for user %s", user_id)
            return
```

- [ ] **Step 6: Run tests**

Run: `cd backend && python -m pytest tests/test_surface_rate_limit.py -v`
Expected: All 5 tests pass.

- [ ] **Step 7: Commit**

```bash
cd backend && git add src/orchestrator/jarvis.py tests/test_surface_rate_limit.py
git commit -m "feat: add surface push rate limiting (5/min workspace, 3/30min insight)"
```

---

### Task 10: Add workspace surface cap with priority-weighted eviction

**Files:**
- Modify: `backend/src/services/surface_mapping.py` (add constants)
- Modify: `backend/src/services/surface_builder.py` (apply cap)
- Modify: `frontend/src/stores/surface-store.ts` (frontend cap)
- Test: `backend/tests/test_surface_rate_limit.py` (add cap tests)

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_surface_rate_limit.py`:

```python
class TestSurfaceCap:
    def test_cap_truncates_to_20(self):
        from src.services.surface_mapping import apply_surface_cap

        # Create 25 summary surfaces
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
        # 15 approvals
        for i in range(15):
            mock = MagicMock()
            mock.kind = "approval"
            mock.created_at = f"2026-04-13T10:{i:02d}:00Z"
            surfaces.append(mock)
        # 10 summaries
        for i in range(10):
            mock = MagicMock()
            mock.kind = "summary"
            mock.created_at = f"2026-04-13T11:{i:02d}:00Z"
            surfaces.append(mock)

        result = apply_surface_cap(surfaces)
        assert len(result) == 20
        approval_count = sum(1 for s in result if s.kind == "approval")
        assert approval_count == 15  # all approvals survive

    def test_higher_priority_survives(self):
        from src.services.surface_mapping import apply_surface_cap

        surfaces = []
        # 10 summaries (tier 6, lowest)
        for i in range(10):
            mock = MagicMock()
            mock.kind = "summary"
            mock.created_at = f"2026-04-13T10:{i:02d}:00Z"
            surfaces.append(mock)
        # 10 plans (tier 1)
        for i in range(10):
            mock = MagicMock()
            mock.kind = "plan"
            mock.created_at = f"2026-04-13T11:{i:02d}:00Z"
            surfaces.append(mock)
        # 5 alerts (tier 2)
        for i in range(5):
            mock = MagicMock()
            mock.kind = "alert"
            mock.created_at = f"2026-04-13T12:{i:02d}:00Z"
            surfaces.append(mock)

        result = apply_surface_cap(surfaces)
        assert len(result) == 20
        plan_count = sum(1 for s in result if s.kind == "plan")
        alert_count = sum(1 for s in result if s.kind == "alert")
        assert plan_count == 10  # all plans survive (tier 1)
        assert alert_count == 5  # all alerts survive (tier 2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_surface_rate_limit.py::TestSurfaceCap -v -x`
Expected: FAIL — `ImportError: cannot import name 'apply_surface_cap'`

- [ ] **Step 3: Add constants and cap function to surface_mapping.py**

Append to `backend/src/services/surface_mapping.py`:

```python
# ── Surface cap ──────────────────────────────────────────────────

MAX_WORKSPACE_SURFACES = 20

PRIORITY_TIERS: dict[str, int] = {
    "approval": 0,
    "plan": 1,
    "alert": 2,
    "briefing": 3,
    "proactive_insight": 4,
    "recommendation": 5,
    "summary": 6,
    "checklist": 6,
    "comparison": 6,
    "timeline": 6,
    "table": 6,
    "activity": 6,
}


def apply_surface_cap(surfaces: list) -> list:
    """Apply priority-weighted cap to workspace surfaces.

    Sorts by (priority_tier, -created_at) and truncates to MAX_WORKSPACE_SURFACES.
    Higher-priority surfaces (lower tier number) survive eviction.
    Within the same tier, newer surfaces (later created_at) survive.

    Uses two-pass stable sort: first by created_at descending (newest first),
    then by tier ascending. Python's stable sort preserves the newest-first
    ordering within each tier.
    """
    if len(surfaces) <= MAX_WORKSPACE_SURFACES:
        return surfaces

    # Pass 1: sort by created_at descending (newest first)
    by_recency = sorted(
        surfaces,
        key=lambda s: getattr(s, "created_at", "") or "",
        reverse=True,
    )
    # Pass 2: stable sort by tier ascending (highest priority first)
    by_priority = sorted(
        by_recency,
        key=lambda s: PRIORITY_TIERS.get(getattr(s, "kind", "summary"), 6),
    )

    return by_priority[:MAX_WORKSPACE_SURFACES]
```

- [ ] **Step 4: Call cap in SurfaceService**

In `backend/src/services/surface_builder.py`, at the end of `build_workspace_surfaces`:

Add import:
```python
from src.services.surface_mapping import apply_surface_cap
```

Change the return:
```python
    async def build_workspace_surfaces(self, user_id: str) -> list[WorkspaceSurfacePush]:
        surfaces: list[WorkspaceSurfacePush] = []
        # ... existing aggregation ...
        return apply_surface_cap(surfaces)
```

- [ ] **Step 5: Add frontend cap in surface store**

In `frontend/src/stores/surface-store.ts`, update `setSurfaces`:

```typescript
  setSurfaces: (surfaces) =>
    set({ surfaces: surfaces.slice(0, 20) }),
```

- [ ] **Step 6: Run tests**

Run: `cd backend && python -m pytest tests/test_surface_rate_limit.py -v`
Expected: All 8 tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/src/services/surface_mapping.py backend/src/services/surface_builder.py frontend/src/stores/surface-store.ts backend/tests/test_surface_rate_limit.py
git commit -m "feat: add workspace surface cap (20) with priority-weighted eviction"
```

---

## Phase 3: Presenter-Driven Surface Architecture

### Task 11: Add SurfaceSpec contract and parser

**Files:**
- Modify: `backend/src/orchestrator/contracts.py` (add SurfaceSpec)
- Modify: `backend/src/services/surface_mapping.py` (add parsers)
- Test: `backend/tests/test_surface_spec.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_surface_spec.py`:

```python
"""Tests for SurfaceSpec contract and parser."""

import pytest
from pydantic import ValidationError


class TestSurfaceSpec:
    def test_valid_spec(self):
        from src.orchestrator.contracts import SurfaceSpec

        spec = SurfaceSpec(
            should_surface=True,
            kind="summary",
            title="Email Summary",
            subtitle="3 unread emails from today",
        )
        assert spec.should_surface is True
        assert spec.kind == "summary"

    def test_title_capped_at_80(self):
        from src.orchestrator.contracts import SurfaceSpec

        spec = SurfaceSpec(
            should_surface=True,
            kind="plan",
            title="A" * 200,
        )
        assert len(spec.title) == 80

    def test_subtitle_capped_at_120(self):
        from src.orchestrator.contracts import SurfaceSpec

        spec = SurfaceSpec(
            should_surface=True,
            kind="plan",
            title="Test",
            subtitle="B" * 200,
        )
        assert len(spec.subtitle) == 120

    def test_invalid_kind_rejected(self):
        from src.orchestrator.contracts import SurfaceSpec

        with pytest.raises(ValidationError):
            SurfaceSpec(should_surface=True, kind="invalid_kind", title="Test")

    def test_metrics_default_empty(self):
        from src.orchestrator.contracts import SurfaceSpec

        spec = SurfaceSpec(should_surface=True, kind="table", title="Data")
        assert spec.metrics == []
        assert spec.tags == []


class TestExtractSurfaceSpec:
    def test_extracts_valid_json_block(self):
        from src.services.surface_mapping import extract_surface_spec

        text = '''Here is your summary.

```json:surface
{"should_surface": true, "kind": "summary", "title": "Email Summary"}
```

The key finding is...'''

        spec = extract_surface_spec(text)
        assert spec is not None
        assert spec.kind == "summary"
        assert spec.title == "Email Summary"

    def test_returns_none_when_no_block(self):
        from src.services.surface_mapping import extract_surface_spec

        spec = extract_surface_spec("Just a plain response with no surface.")
        assert spec is None

    def test_returns_none_on_malformed_json(self):
        from src.services.surface_mapping import extract_surface_spec

        text = '''Response.

```json:surface
{invalid json here}
```'''

        spec = extract_surface_spec(text)
        assert spec is None

    def test_returns_none_when_should_surface_false(self):
        from src.services.surface_mapping import extract_surface_spec

        text = '''Response.

```json:surface
{"should_surface": false, "kind": "summary", "title": "Test"}
```'''

        spec = extract_surface_spec(text)
        assert spec is not None
        assert spec.should_surface is False


class TestExtractSurfaceData:
    def test_extracts_surface_data_block(self):
        from src.services.surface_mapping import extract_surface_data

        text = '''Response.

```json:surface
{"should_surface": true, "kind": "table", "title": "PRs"}
```

```json:surface_data
{"columns": [{"key": "title"}], "rows": [{"title": "Fix bug"}]}
```'''

        data = extract_surface_data(text)
        assert data is not None
        assert "columns" in data
        assert len(data["rows"]) == 1

    def test_returns_none_when_no_data_block(self):
        from src.services.surface_mapping import extract_surface_data

        data = extract_surface_data("No data block here.")
        assert data is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_surface_spec.py -v -x`
Expected: FAIL — `ImportError: cannot import name 'SurfaceSpec'`

- [ ] **Step 3: Add SurfaceSpec to contracts.py**

In `backend/src/orchestrator/contracts.py`, add after the `InsightSurfaceData` class:

```python
class SurfaceSpec(BaseModel):
    """Surface specification produced by the Presenter agent.

    The Presenter decides IF a surface should be created, what KIND it is,
    and what PREVIEW data to show. Parsed from the Presenter's JSON output.
    """

    model_config = ConfigDict(extra="ignore")

    should_surface: bool = False
    kind: SurfaceKind
    title: str
    subtitle: str | None = None
    status: (
        Literal["pending", "running", "completed", "failed", "awaiting_approval", "cancelled", "proposal"]
        | None
    ) = None
    priority: Literal["low", "medium", "high", "critical"] | None = None
    metrics: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _cap_title(cls, v: str) -> str:
        return v[:80]

    @field_validator("subtitle")
    @classmethod
    def _cap_subtitle(cls, v: str | None) -> str | None:
        return v[:120] if v else None
```

Add `field_validator` to the imports if not already present:
```python
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
```

- [ ] **Step 4: Add parsers to surface_mapping.py**

Append to `backend/src/services/surface_mapping.py`:

```python
import json
import logging
import re

logger = logging.getLogger(__name__)

_SURFACE_SPEC_RE = re.compile(r"```json:surface\s*\n(.*?)\n```", re.DOTALL)
_SURFACE_DATA_RE = re.compile(r"```json:surface_data\s*\n(.*?)\n```", re.DOTALL)


def extract_surface_spec(response_text: str):
    """Extract SurfaceSpec from ```json:surface``` block in Presenter response.

    Returns SurfaceSpec on success, None if not found or invalid.
    Best-effort — degrades to chat-only on failure.
    """
    from src.orchestrator.contracts import SurfaceSpec

    match = _SURFACE_SPEC_RE.search(response_text)
    if not match:
        return None

    try:
        data = json.loads(match.group(1))
        return SurfaceSpec(**data)
    except (json.JSONDecodeError, Exception):
        logger.debug("Failed to parse SurfaceSpec from response", exc_info=True)
        return None


def extract_surface_data(response_text: str) -> dict | None:
    """Extract structured data from ```json:surface_data``` block.

    Used by detail tab builders for comparison, table, timeline, checklist kinds.
    """
    match = _SURFACE_DATA_RE.search(response_text)
    if not match:
        return None

    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        logger.debug("Failed to parse surface_data from response", exc_info=True)
        return None
```

- [ ] **Step 5: Run tests**

Run: `cd backend && python -m pytest tests/test_surface_spec.py -v`
Expected: All 10 tests pass.

- [ ] **Step 6: Commit**

```bash
cd backend && git add src/orchestrator/contracts.py src/services/surface_mapping.py tests/test_surface_spec.py
git commit -m "feat: add SurfaceSpec contract and response parsers"
```

---

### Task 12: Update Presenter prompt with SURFACE_GENERATION section

**Files:**
- Modify: `backend/src/orchestrator/prompts.py:552-592`

- [ ] **Step 1: Add SURFACE_GENERATION section to PRESENTER_PROMPT**

In `backend/src/orchestrator/prompts.py`, replace the existing `PRESENTER_PROMPT` (lines 552-592) with:

```python
PRESENTER_PROMPT = """\
<role>
You are the Presenter agent in Jarvis — the ONLY voice the user hears.
Your job is to take raw outputs from other agents (plans, research, observations,
decisions) and format them into clear, conversational responses for the user.
You do NOT make decisions. You do NOT take actions. You present.
</role>

<rules>
1. Be conversational and natural — not robotic or formulaic
2. Lead with what matters most to the user
3. Be concise when the user is busy, detailed when they are exploring
4. Never expose internal IDs, trace IDs, or system internals
5. If an action requires user approval, clearly state what and why
6. If something failed, explain what happened simply
7. Group related information together
8. Format appropriately: markdown for web, plain text for Telegram
9. When presenting data (emails, calendar), use clear structure
10. End with recommended next steps when appropriate
</rules>

<surface_generation>
When your response has visual value beyond chat text, include a surface specification
in a ```json:surface``` fenced block. This creates a persistent workspace card.

Choose the surface kind that best fits the information shape:

| Kind | When to use |
|------|-------------|
| summary | Single-topic synthesis, lookup result, brief answer with sources |
| briefing | Daily overview, multi-source digest, morning context |
| plan | Multi-step execution with progress tracking |
| checklist | Sequential low-risk tasks in the same category |
| comparison | Side-by-side evaluation of 2+ alternatives |
| alert | Blocked execution, system warning, urgent attention needed |
| timeline | Chronologically ordered events or history narrative |
| table | Structured tabular data, multiple entities with shared attributes |
| recommendation | Suggested action based on observed patterns |
| activity | Summary of recent Jarvis actions (only when user asks) |

Do NOT create a surface when:
- The response is a simple conversational reply (greeting, acknowledgment, clarification)
- The information fits naturally in chat text alone
- The user explicitly asked for a text response

Do NOT use these kinds (system-generated only):
- approval (created by TrustEngine)
- proactive_insight (created by perception pipeline)

When you create a surface, still include a brief chat response summarizing the key point.
The surface provides the detailed, persistent, interactive view.

For structured data (comparison options, table rows, timeline events), include a
```json:surface_data``` block with the structured payload alongside the surface spec.

Example surface spec:
```json:surface
{
  "should_surface": true,
  "kind": "table",
  "title": "Open Pull Requests",
  "subtitle": "5 PRs across 3 repos need attention",
  "priority": "medium",
  "metrics": [{"label": "Open", "value": "5", "variant": "warning"}],
  "tags": ["github"]
}
```
</surface_generation>

<examples>
Plan goal: draft a follow-up email to investor
→ "I've drafted a follow-up email to John about the investor meeting. The draft is in your Gmail — \
review it and let me know if you'd like changes before sending."

Plan goal: check email for updates
→ "You have 5 unread emails. The most important is from Sarah Chen about the Series A term sheet — \
she's asking for a response by Friday. Two others are newsletters, and two are meeting invites."

Plan goal: research competitor Acme Corp
→ "Here's what I found about Acme Corp: [structured findings]. Key takeaway: they raised $10M \
last quarter and are expanding into your market segment. Want me to dig deeper into their product?"

Something failed:
→ "I wasn't able to check your Gmail — it looks like the connection needs to be re-authorized. \
You can fix this in Settings → Connectors."
</examples>
"""
```

Note: Rule 11 (about surfaces being built by infrastructure) is **removed** — the Presenter now owns surface generation.

- [ ] **Step 2: Run ruff**

Run: `cd backend && ruff check src/orchestrator/prompts.py`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
cd backend && git add src/orchestrator/prompts.py
git commit -m "feat: add SURFACE_GENERATION section to Presenter prompt"
```

---

### Task 13: Replace hardcoded surface push with Presenter-driven path

**Files:**
- Modify: `backend/src/orchestrator/jarvis.py` (add `_push_presenter_surface`, update call sites)
- Modify: `backend/src/services/surface_mapping.py` (delete old functions)

- [ ] **Step 1: Add `_push_presenter_surface` to JarvisOrchestrator**

In `backend/src/orchestrator/jarvis.py`, add this method to the `JarvisOrchestrator` class (near `_push_workspace_surface`):

```python
    async def _push_presenter_surface(
        self,
        spec,
        user_id: str,
        workspace_id: str,
        run_id: str | None = None,
        response_text: str = "",
    ) -> str | None:
        """Push a Presenter-specified surface to the workspace.

        Builds WorkspaceSurfacePush from a SurfaceSpec produced by the Presenter agent.
        """
        from datetime import datetime, timedelta, timezone

        from ulid import ULID

        from src.orchestrator.contracts import WorkspaceSurfacePush
        from src.services.surface_mapping import extract_surface_data
        from src.ui.contracts import SurfaceMetric, SurfacePreview
        from src.ui.renderer import build_detail_config

        if not await self._check_surface_rate(user_id, "workspace"):
            logger.debug("Presenter surface rate-limited for user %s", user_id)
            return None

        try:
            event_bus = await self._ensure_event_bus()
            if not event_bus:
                return None

            surface_id = f"surf_{ULID()}"
            preview = SurfacePreview(
                title=spec.title,
                subtitle=spec.subtitle,
                status=spec.status,
                priority=spec.priority,
                metrics=[SurfaceMetric(**m) for m in spec.metrics] if spec.metrics else [],
                tags=spec.tags or [],
            )
            detail_config = build_detail_config(spec.kind, surface_id)

            surface = WorkspaceSurfacePush(
                id=surface_id,
                kind=spec.kind,
                preview=preview.model_dump(mode="json"),
                detail_config=(detail_config.model_dump(mode="json") if detail_config else None),
                source_run_id=run_id,
                response_preview=(response_text[:300] if response_text else None),
                created_at=datetime.now(timezone.utc).isoformat(),
            )

            channel = f"jarvis:a2ui:{user_id}"
            ws_msg = json.dumps({"type": "surface", "surface": surface.model_dump(mode="json")})
            await event_bus.publish_to_channel(channel, ws_msg)

            # Persist to DB
            try:
                from src.models.ui_state import UISurface

                surface_data = extract_surface_data(response_text)

                async with self._db_factory() as db:
                    payload = surface.model_dump(mode="json")
                    if surface_data:
                        payload["surface_data"] = surface_data

                    db.add(
                        UISurface(
                            surface_id=surface.id,
                            user_id=user_id,
                            workspace_id=workspace_id,
                            surface_type=spec.kind,
                            payload=payload,
                            preview=preview.model_dump(mode="json"),
                            detail_config=(
                                detail_config.model_dump(mode="json") if detail_config else None
                            ),
                            expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
                        )
                    )
                    await db.commit()
            except Exception:
                logger.debug("Failed to persist presenter surface", exc_info=True)

            return surface_id
        except Exception:
            logger.warning("Failed to push presenter surface", exc_info=True)
            return None
```

- [ ] **Step 2: Update call sites in process_message**

In `backend/src/orchestrator/jarvis.py`, find the `_push_workspace_surface` call in `process_message` (around line 976):

Replace:
```python
            surface_id = await self._push_workspace_surface(
                plan,
                user_id,
                workspace_id,
                run_id=result.get("run_id"),
                response_text=result.get("presentation", result.get("presenter", "")),
            )
```

With:
```python
            response_text = result.get("presentation", result.get("presenter", ""))
            surface_spec = extract_surface_spec(response_text)
            if surface_spec and surface_spec.should_surface:
                surface_id = await self._push_presenter_surface(
                    spec=surface_spec,
                    user_id=user_id,
                    workspace_id=workspace_id,
                    run_id=result.get("run_id"),
                    response_text=response_text,
                )
            else:
                surface_id = None
```

Add import near the top:
```python
from src.services.surface_mapping import extract_surface_spec
```

- [ ] **Step 3: Update call site in process_message_stream**

Find the `_push_workspace_surface` call in `process_message_stream` (around line 1313):

Replace:
```python
            try:
                surface_id = await self._push_workspace_surface(
                    plan,
                    user_id,
                    workspace_id,
                    run_id=None,
                    response_text=presenter_text,
                )
            except Exception:
                logger.warning("Surface push failed", exc_info=True)
```

With:
```python
            try:
                surface_spec = extract_surface_spec(presenter_text)
                if surface_spec and surface_spec.should_surface:
                    surface_id = await self._push_presenter_surface(
                        spec=surface_spec,
                        user_id=user_id,
                        workspace_id=workspace_id,
                        run_id=None,
                        response_text=presenter_text,
                    )
            except Exception:
                logger.warning("Surface push failed", exc_info=True)
```

- [ ] **Step 4: Keep `_push_workspace_surface` for briefing scheduler path**

The briefing scheduler call at line ~2027 uses `_push_workspace_surface` with a synthetic PlanOutput. This path doesn't go through the Presenter, so keep `_push_workspace_surface` for now. It will be refactored when the briefing pipeline is updated to use the Presenter.

- [ ] **Step 5: Delete old functions from surface_mapping.py**

In `backend/src/services/surface_mapping.py`, delete `derive_surface_kind` and `build_surface_preview_from_plan`. Keep the imports they need (they're now used by the parsers and cap function).

Also remove the import of these functions in jarvis.py if no longer called (check the briefing path first — if `_push_workspace_surface` still uses them internally via its own local import, they may need to stay or be moved into that method).

- [ ] **Step 6: Run tests**

Run: `cd backend && python -m pytest tests/ -v -x -k "orchestrator or surface"`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
cd backend && git add src/orchestrator/jarvis.py src/services/surface_mapping.py
git commit -m "feat: replace hardcoded surface push with Presenter-driven SurfaceSpec path"
```

---

## Phase 4: Detail Tabs + Enrichment

### Task 14: Update _TABS_BY_KIND and add new tab builders

**Files:**
- Modify: `backend/src/ui/renderer.py:394-401` (_TABS_BY_KIND)
- Modify: `backend/src/services/surface_detail_builders.py` (add 14 builders, modify 1)
- Modify: `backend/src/api/routes_surface_detail.py` (add surf_ prefix)
- Test: `backend/tests/test_detail_tabs_new.py`

This is the largest task. Given its size, the implementation plan provides the structure and key builders. Each builder follows the same pattern established by existing builders in `surface_detail_builders.py`.

- [ ] **Step 1: Update _TABS_BY_KIND in renderer.py**

Replace the `_TABS_BY_KIND` dict in `backend/src/ui/renderer.py`:

```python
_TABS_BY_KIND: dict[str, list[tuple[str, str]]] = {
    "plan": [("overview", "Overview"), ("context", "Context"), ("execution", "Execution")],
    "summary": [("overview", "Overview"), ("sources", "Sources"), ("context", "Context")],
    "briefing": [("priorities", "Priorities"), ("events", "Events"), ("actions", "Actions")],
    "approval": [("request", "Request"), ("risk", "Risk"), ("history", "History")],
    "recommendation": [("overview", "Overview"), ("evidence", "Evidence"), ("context", "Context")],
    "alert": [("overview", "Overview"), ("diagnostics", "Diagnostics")],
    "checklist": [("items", "Items"), ("context", "Context")],
    "comparison": [("options", "Options"), ("criteria", "Criteria")],
    "timeline": [("events", "Events"), ("context", "Context")],
    "table": [("data", "Data"), ("sources", "Sources")],
    "activity": [("runs", "Recent Runs"), ("stats", "Stats")],
    "proactive_insight": [("signal", "Signal"), ("actions", "Actions"), ("context", "Context")],
}
```

- [ ] **Step 2: Write failing tests for new builders**

Create `backend/tests/test_detail_tabs_new.py`:

```python
"""Tests for new detail tab builders added in Phase 4."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _mock_surface(surface_id: str = "surf_test", surface_type: str = "summary", payload: dict | None = None):
    s = MagicMock()
    s.surface_id = surface_id
    s.surface_type = surface_type
    s.payload = payload or {}
    s.workspace_id = "ws_test"
    return s


class TestInsightTabBuilders:
    @pytest.mark.asyncio
    async def test_build_insight_signal_with_data(self):
        from src.services.surface_detail_builders import build_insight_signal

        surface = _mock_surface(
            surface_type="proactive_insight",
            payload={
                "insight_data": {
                    "signal_source": "gmail",
                    "signal_summary": "New email from investor",
                    "relevance_score": 0.85,
                    "relevance_reasoning": "Matches active fundraising goal",
                }
            },
        )
        mock_db = AsyncMock()
        result = await build_insight_signal(mock_db, surface)
        assert result.tab_id == "signal"
        assert len(result.sections) > 0

    @pytest.mark.asyncio
    async def test_build_insight_signal_empty_payload(self):
        from src.services.surface_detail_builders import build_insight_signal

        surface = _mock_surface(surface_type="proactive_insight", payload={})
        mock_db = AsyncMock()
        result = await build_insight_signal(mock_db, surface)
        assert result.tab_id == "signal"
        # Should return an empty/fallback section

    @pytest.mark.asyncio
    async def test_build_insight_actions(self):
        from src.services.surface_detail_builders import build_insight_actions

        surface = _mock_surface(
            surface_type="proactive_insight",
            payload={
                "insight_data": {
                    "suggested_actions": [
                        {"description": "Reply to email", "capability": "email.send", "action_input": {}, "action_preview": ""},
                    ]
                }
            },
        )
        mock_db = AsyncMock()
        result = await build_insight_actions(mock_db, surface)
        assert result.tab_id == "actions"
        assert len(result.sections) > 0


class TestActivityTabBuilders:
    @pytest.mark.asyncio
    async def test_build_activity_runs_no_runs(self):
        from src.services.surface_detail_builders import build_activity_runs

        surface = _mock_surface(surface_type="activity")
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await build_activity_runs(mock_db, surface)
        assert result.tab_id == "runs"


class TestRecommendationEvidence:
    @pytest.mark.asyncio
    async def test_build_recommendation_evidence_no_runs(self):
        from src.services.surface_detail_builders import build_recommendation_evidence

        surface = _mock_surface(
            surface_type="recommendation",
            payload={"preview": {"title": "Investigate 2 failed runs"}},
        )
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await build_recommendation_evidence(mock_db, surface)
        assert result.tab_id == "evidence"


class TestTabBuildersRegistry:
    def test_all_30_builders_registered(self):
        from src.services.surface_detail_builders import TAB_BUILDERS

        assert len(TAB_BUILDERS) == 30

    def test_all_surface_kinds_have_tabs(self):
        from src.services.surface_detail_builders import TAB_BUILDERS

        kinds_with_tabs = {k for k, _ in TAB_BUILDERS.keys()}
        expected = {
            "plan", "summary", "briefing", "approval", "recommendation", "alert",
            "checklist", "comparison", "timeline", "table", "activity", "proactive_insight",
        }
        assert kinds_with_tabs == expected
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_detail_tabs_new.py -v -x`
Expected: FAIL — `ImportError: cannot import name 'build_insight_signal'`

- [ ] **Step 4: Add all new tab builders to surface_detail_builders.py**

Append the following builders to `backend/src/services/surface_detail_builders.py` before the `TAB_BUILDERS` registry:

```python
# ── Checklist builders ─────────────────────────────────────────


async def build_checklist_items(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Checklist items — steps rendered as a check list."""
    from src.models.task_graph import TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        payload = _get_payload(surface)
        data = payload.get("surface_data", {})
        if data and isinstance(data, dict) and "items" in data:
            children: list[A2UIComponent] = []
            for i, item in enumerate(data["items"][:30]):
                title = item.get("title", "") if isinstance(item, dict) else str(item)
                done = item.get("done", False) if isinstance(item, dict) else False
                variant = "success" if done else "default"
                children.append(
                    r.row(f"item_{i}", [
                        r.badge(f"item_{i}_st", "done" if done else "pending", variant=variant),
                        r.text(f"item_{i}_title", title),
                    ])
                )
            if children:
                return DetailTabResponse(
                    tab_id="items",
                    sections=[_section("items", f"Items ({len(children)})", children, collapsed=False)],
                )
        return _empty_tab("items", "No checklist items available.")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())
    if not steps:
        return _empty_tab("items", "No steps recorded.")

    children = []
    for i, step in enumerate(steps):
        done = step.status == "completed"
        variant = "success" if done else "default"
        children.append(
            r.row(f"chk_{i}", [
                r.badge(f"chk_{i}_st", "done" if done else step.status or "pending", variant=variant),
                r.text(f"chk_{i}_name", step.name or _get_step_desc(step) or f"Step {i + 1}"),
            ])
        )

    completed = sum(1 for s in steps if s.status == "completed")
    return DetailTabResponse(
        tab_id="items",
        sections=[_section("items", f"Items ({completed}/{len(steps)})", children, collapsed=False)],
    )


async def build_checklist_context(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Checklist context — reuse plan context pattern."""
    return await build_plan_context(db, surface)


# ── Comparison builders ────────────────────────────────────────


async def build_comparison_options(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Comparison options from Presenter surface_data."""
    payload = _get_payload(surface)
    data = payload.get("surface_data", {})

    if data and isinstance(data, dict) and "options" in data:
        children: list[A2UIComponent] = []
        for i, opt in enumerate(data["options"][:10]):
            name = opt.get("name", f"Option {i + 1}") if isinstance(opt, dict) else str(opt)
            desc = opt.get("description", "") if isinstance(opt, dict) else ""
            pros = opt.get("pros", []) if isinstance(opt, dict) else []
            cons = opt.get("cons", []) if isinstance(opt, dict) else []

            opt_children: list[A2UIComponent] = [r.text(f"opt_{i}_name", name, variant="heading")]
            if desc:
                opt_children.append(r.caption(f"opt_{i}_desc", desc))
            for j, pro in enumerate(pros[:5]):
                opt_children.append(r.badge(f"opt_{i}_pro_{j}", f"+ {pro}", variant="success"))
            for j, con in enumerate(cons[:5]):
                opt_children.append(r.badge(f"opt_{i}_con_{j}", f"- {con}", variant="danger"))

            children.append(r.card(f"opt_{i}", opt_children))

        if children:
            return DetailTabResponse(
                tab_id="options",
                sections=[_section("options", f"Options ({len(children)})", children, collapsed=False)],
            )

    text_content = payload.get("response_preview", "") or ""
    return DetailTabResponse(
        tab_id="options",
        sections=[_section("content", "Options", [r.text("opt_text", text_content or "No options data available.")], collapsed=False)],
    )


async def build_comparison_criteria(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Comparison criteria from surface_data."""
    payload = _get_payload(surface)
    data = payload.get("surface_data", {})

    if data and isinstance(data, dict) and "criteria" in data:
        children: list[A2UIComponent] = []
        for i, criterion in enumerate(data["criteria"][:20]):
            label = criterion if isinstance(criterion, str) else criterion.get("name", f"Criterion {i + 1}")
            children.append(r.badge(f"crit_{i}", label))

        if children:
            return DetailTabResponse(
                tab_id="criteria",
                sections=[_section("criteria", "Evaluation Criteria", children, collapsed=False)],
            )

    return _empty_tab("criteria", "No criteria data available.")


# ── Timeline builders ──────────────────────────────────────────


async def build_timeline_events(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Timeline events from surface_data or perception events."""
    payload = _get_payload(surface)
    data = payload.get("surface_data", {})

    if data and isinstance(data, dict) and "events" in data:
        events = data["events"][:30]
        return DetailTabResponse(
            tab_id="events",
            sections=[_section("timeline", "Events", [r.timeline("tl", events)], collapsed=False)],
        )

    return await build_briefing_events(db, surface)


async def build_timeline_context(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Timeline context — reuse plan context pattern."""
    return await build_plan_context(db, surface)


# ── Table builders ─────────────────────────────────────────────


async def build_table_data(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Table data from Presenter surface_data."""
    payload = _get_payload(surface)
    data = payload.get("surface_data", {})

    if data and isinstance(data, dict) and "columns" in data and "rows" in data:
        return DetailTabResponse(
            tab_id="data",
            sections=[_section(
                "table",
                f"Data ({len(data['rows'])} rows)",
                [r.table("tbl", data["columns"], data["rows"])],
                collapsed=False,
            )],
        )

    text_content = payload.get("response_preview", "") or ""
    return DetailTabResponse(
        tab_id="data",
        sections=[_section("content", "Data", [r.text("tbl_text", text_content or "No table data available.")], collapsed=False)],
    )


async def build_table_sources(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Table sources — tool calls that produced the data."""
    from src.models.task_graph import TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("sources", "No linked execution run.")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.created_at)
    )
    steps = list(steps_result.scalars().all())
    if not steps:
        return _empty_tab("sources", "No execution steps recorded.")

    children: list[A2UIComponent] = []
    for i, step in enumerate(steps):
        step_type = step.step_type or step.name or f"Step {i + 1}"
        children.append(
            r.row(f"src_{i}", [
                r.badge(f"src_{i}_type", step_type),
                r.caption(f"src_{i}_time", _format_ts(step.created_at)),
            ])
        )

    return DetailTabResponse(
        tab_id="sources",
        sections=[_section("tools", f"Source Steps ({len(children)})", children, collapsed=False)],
    )


# ── Activity builders ──────────────────────────────────────────


async def build_activity_runs(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Recent runs — last 24h of TaskRuns for this workspace."""
    from src.models.task_graph import TaskRun

    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        payload = _get_payload(surface)
        ws_id = payload.get("workspace_id")

    if not ws_id:
        return _empty_tab("runs", "Could not resolve workspace.")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(TaskRun)
        .where(TaskRun.workspace_id == ws_id, TaskRun.created_at >= cutoff)
        .order_by(TaskRun.created_at.desc())
        .limit(20)
    )
    runs = list(result.scalars().all())
    if not runs:
        return _empty_tab("runs", "No runs in the last 24 hours.")

    children: list[A2UIComponent] = []
    for i, run in enumerate(runs):
        variant = "success" if run.status == "completed" else ("danger" if run.status == "failed" else "default")
        children.append(
            r.row(f"run_{i}", [
                r.badge(f"run_{i}_st", run.status or "unknown", variant=variant),
                r.text(f"run_{i}_src", run.source or "unknown"),
                r.caption(f"run_{i}_time", _format_ts(run.created_at)),
            ])
        )

    return DetailTabResponse(
        tab_id="runs",
        sections=[_section("runs", f"Recent Runs ({len(runs)})", children, collapsed=False)],
    )


async def build_activity_stats(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Activity stats — aggregated metrics for last 24h."""
    from sqlalchemy import func as sa_func

    from src.models.task_graph import TaskRun

    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        payload = _get_payload(surface)
        ws_id = payload.get("workspace_id")

    if not ws_id:
        return _empty_tab("stats", "Could not resolve workspace.")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    total_result = await db.execute(
        select(sa_func.count(TaskRun.run_id)).where(
            TaskRun.workspace_id == ws_id, TaskRun.created_at >= cutoff
        )
    )
    total = total_result.scalar() or 0

    completed_result = await db.execute(
        select(sa_func.count(TaskRun.run_id)).where(
            TaskRun.workspace_id == ws_id, TaskRun.status == "completed", TaskRun.created_at >= cutoff
        )
    )
    completed = completed_result.scalar() or 0

    failed_result = await db.execute(
        select(sa_func.count(TaskRun.run_id)).where(
            TaskRun.workspace_id == ws_id, TaskRun.status == "failed", TaskRun.created_at >= cutoff
        )
    )
    failed = failed_result.scalar() or 0

    success_rate = (completed / total * 100) if total > 0 else 0

    children: list[A2UIComponent] = [
        r.metric("stat_total", "Total Runs", total),
        r.metric("stat_completed", "Completed", completed),
        r.metric("stat_failed", "Failed", failed, trend="down" if failed > 0 else None),
        r.progress("stat_success", success_rate, label=f"Success Rate: {success_rate:.0f}%"),
    ]

    return DetailTabResponse(
        tab_id="stats",
        sections=[_section("stats", "24h Stats", children, collapsed=False)],
    )


# ── Proactive insight builders ─────────────────────────────────


async def build_insight_signal(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Insight signal — the perception event that triggered this insight."""
    payload = _get_payload(surface)
    insight = payload.get("insight_data", {})

    if not insight:
        return _empty_tab("signal", "No insight data available.")

    children: list[A2UIComponent] = []
    if insight.get("signal_source"):
        children.append(r.badge("sig_source", insight["signal_source"]))
    if insight.get("signal_category"):
        children.append(r.badge("sig_cat", insight["signal_category"]))
    if insight.get("signal_summary"):
        children.append(r.text("sig_summary", insight["signal_summary"]))
    if insight.get("relevance_score") is not None:
        children.append(r.metric("sig_relevance", "Relevance", f"{insight['relevance_score']:.0%}"))
    if insight.get("relevance_reasoning"):
        children.append(r.caption("sig_reasoning", insight["relevance_reasoning"]))

    if not children:
        return _empty_tab("signal", "No signal details available.")

    return DetailTabResponse(
        tab_id="signal",
        sections=[_section("signal", "Signal Details", children, collapsed=False)],
    )


async def build_insight_actions(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Suggested actions from the insight."""
    payload = _get_payload(surface)
    insight = payload.get("insight_data", {})
    actions = insight.get("suggested_actions", [])

    if not actions:
        return _empty_tab("actions", "No suggested actions.")

    children: list[A2UIComponent] = []
    for i, action in enumerate(actions[:10]):
        desc = action.get("description", "") if isinstance(action, dict) else str(action)
        cap = action.get("capability", "") if isinstance(action, dict) else ""
        action_children: list[A2UIComponent] = [r.text(f"act_{i}_desc", desc)]
        if cap:
            action_children.append(r.badge(f"act_{i}_cap", cap))
        action_children.append(
            r.button(
                f"act_{i}_exec",
                "Execute",
                variant="primary",
                action_payload={"action": "execute_insight", "surface_id": payload.get("id", ""), "action_index": i},
            )
        )
        children.append(r.card(f"act_{i}", action_children))

    return DetailTabResponse(
        tab_id="actions",
        sections=[_section("actions", f"Suggested Actions ({len(children)})", children, collapsed=False)],
    )


async def build_insight_context(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Related goals and context for insight."""
    payload = _get_payload(surface)
    insight = payload.get("insight_data", {})
    goals = insight.get("related_goals", [])

    children: list[A2UIComponent] = []
    for i, goal in enumerate(goals[:10]):
        children.append(r.text(f"goal_{i}", goal))

    if not children:
        return _empty_tab("context", "No related context available.")

    return DetailTabResponse(
        tab_id="context",
        sections=[_section("goals", "Related Goals", children, collapsed=False)],
    )


# ── Enriched recommendation evidence ──────────────────────────


async def build_recommendation_evidence(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Evidence for the recommendation — linked failed runs or source errors."""
    from src.models.task_graph import TaskRun

    payload = _get_payload(surface)
    preview = payload.get("preview", {})
    title = preview.get("title", "") if isinstance(preview, dict) else ""

    ws_id = getattr(surface, "workspace_id", None)
    if not ws_id:
        ws_id = payload.get("workspace_id")

    sections: list[DetailSection] = []

    if ws_id and "failed" in title.lower():
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        result = await db.execute(
            select(TaskRun)
            .where(TaskRun.workspace_id == ws_id, TaskRun.status == "failed", TaskRun.updated_at >= cutoff)
            .order_by(TaskRun.updated_at.desc())
            .limit(10)
        )
        runs = list(result.scalars().all())
        if runs:
            children: list[A2UIComponent] = []
            for i, run in enumerate(runs):
                err_msg = ""
                if run.error and isinstance(run.error, dict):
                    err_msg = _truncate(run.error.get("message", str(run.error)), 100)
                children.append(
                    r.row(f"fail_{i}", [
                        r.badge(f"fail_{i}_st", "failed", variant="danger"),
                        r.text(f"fail_{i}_src", run.source or "unknown"),
                        r.caption(f"fail_{i}_err", err_msg) if err_msg else r.text(f"fail_{i}_na", "No error details"),
                        r.caption(f"fail_{i}_time", _format_ts(run.updated_at)),
                    ])
                )
            sections.append(_section("failed_runs", f"Failed Runs ({len(runs)})", children, collapsed=False))

    if ws_id and ("source" in title.lower() or "failing" in title.lower()):
        try:
            from src.models.perception_state import PerceptionState

            result = await db.execute(
                select(PerceptionState)
                .where(PerceptionState.workspace_id == ws_id, PerceptionState.circuit_state == "open")
                .limit(5)
            )
            stale = list(result.scalars().all())
            if stale:
                children = []
                for i, s in enumerate(stale):
                    children.append(
                        r.row(f"src_{i}", [
                            r.badge(f"src_{i}_name", s.source, variant="danger"),
                            r.text(f"src_{i}_fails", f"{s.consecutive_failures} consecutive failures"),
                        ])
                    )
                sections.append(_section("failing_sources", f"Failing Sources ({len(stale)})", children, collapsed=False))
        except Exception:
            logger.debug("Failed to check perception state", exc_info=True)

    if not sections:
        return _empty_tab("evidence", "No supporting evidence available.")
    return DetailTabResponse(tab_id="evidence", sections=sections)


# ── Alert diagnostics ──────────────────────────────────────────


async def build_alert_diagnostics(db: AsyncSession, surface: Any, **kwargs: Any) -> DetailTabResponse:
    """Alert diagnostics — step-level details for blocked/failed runs."""
    from src.models.task_graph import TaskStep

    run_id = _extract_run_id(surface)
    if not run_id:
        return _empty_tab("diagnostics", "No linked execution run.")

    steps_result = await db.execute(
        select(TaskStep).where(TaskStep.run_id == run_id).order_by(TaskStep.step_order)
    )
    steps = list(steps_result.scalars().all())
    if not steps:
        return _empty_tab("diagnostics", "No execution steps recorded.")

    children: list[A2UIComponent] = []
    for i, step in enumerate(steps):
        if step.status not in ("failed", "blocked", "timed_out"):
            continue
        step_children: list[A2UIComponent] = [
            r.badge(f"diag_{i}_st", step.status, variant="danger"),
            r.text(f"diag_{i}_name", step.name or _get_step_desc(step) or f"Step {i + 1}"),
        ]
        if step.output_data and isinstance(step.output_data, dict):
            err = step.output_data.get("error", "")
            if err:
                step_children.append(r.alert(f"diag_{i}_err", _truncate(str(err), 200), severity="error"))
        children.append(r.card(f"diag_{i}", step_children))

    if not children:
        return _empty_tab("diagnostics", "No failed or blocked steps found.")

    return DetailTabResponse(
        tab_id="diagnostics",
        sections=[_section("diagnostics", f"Problem Steps ({len(children)})", children, collapsed=False)],
    )
```

- [ ] **Step 5: Update the TAB_BUILDERS registry**

Replace the `TAB_BUILDERS` dict at the end of `surface_detail_builders.py`:

```python
TAB_BUILDERS: dict[tuple[str, str], Any] = {
    # Plan
    ("plan", "overview"): build_plan_overview,
    ("plan", "context"): build_plan_context,
    ("plan", "execution"): build_plan_execution,
    # Summary
    ("summary", "overview"): build_summary_overview,
    ("summary", "sources"): build_summary_sources,
    ("summary", "context"): build_summary_context,
    # Briefing
    ("briefing", "priorities"): build_briefing_priorities,
    ("briefing", "events"): build_briefing_events,
    ("briefing", "actions"): build_briefing_actions,
    # Approval
    ("approval", "request"): build_approval_request,
    ("approval", "risk"): build_approval_risk,
    ("approval", "history"): build_approval_history,
    # Recommendation
    ("recommendation", "overview"): build_recommendation_overview,
    ("recommendation", "evidence"): build_recommendation_evidence,
    ("recommendation", "context"): build_recommendation_context,
    # Alert
    ("alert", "overview"): build_alert_overview,
    ("alert", "diagnostics"): build_alert_diagnostics,
    # Checklist
    ("checklist", "items"): build_checklist_items,
    ("checklist", "context"): build_checklist_context,
    # Comparison
    ("comparison", "options"): build_comparison_options,
    ("comparison", "criteria"): build_comparison_criteria,
    # Timeline
    ("timeline", "events"): build_timeline_events,
    ("timeline", "context"): build_timeline_context,
    # Table
    ("table", "data"): build_table_data,
    ("table", "sources"): build_table_sources,
    # Activity
    ("activity", "runs"): build_activity_runs,
    ("activity", "stats"): build_activity_stats,
    # Proactive insight
    ("proactive_insight", "signal"): build_insight_signal,
    ("proactive_insight", "actions"): build_insight_actions,
    ("proactive_insight", "context"): build_insight_context,
}
```

Note: This is 30 entries total. The old `("recommendation", "context"): build_recommendation_context` is kept alongside the new `evidence` tab — both tabs exist (the recommendation kind has 3 tabs: overview, evidence, context).

- [ ] **Step 6: Update ephemeral prefix map**

In `backend/src/api/routes_surface_detail.py`, add `surf_` prefix handling. Change `_PREFIX_MAP`:

```python
_PREFIX_MAP: dict[str, tuple[str, str]] = {
    "approval_": ("approval", "approval_id"),
    "briefing_": ("briefing", "briefing_id"),
    "priority_": ("alert", "run_id"),
    "rec_": ("recommendation", "index"),
    "exec_": ("plan", "run_id"),
    "surf_": ("_from_db", "surface_id"),
}
```

And update `_resolve_ephemeral` to handle the `_from_db` marker:

```python
def _resolve_ephemeral(surface_id: str) -> tuple[str, dict] | None:
    """Resolve kind and metadata from an ephemeral surface_id prefix."""
    for prefix, (kind, ref_key) in _PREFIX_MAP.items():
        if surface_id.startswith(prefix):
            if kind == "_from_db":
                return None  # force DB lookup path
            ref_value = surface_id[len(prefix):]
            return kind, {ref_key: ref_value, "surface_id": surface_id}
    return None
```

For `surf_`-prefixed surfaces, `_resolve_ephemeral` returns None, forcing the DB lookup path which reads `UISurface.surface_type` for the kind. This is the correct behavior — Presenter-generated surfaces are always persisted.

- [ ] **Step 7: Run tests**

Run: `cd backend && python -m pytest tests/test_detail_tabs_new.py -v`
Expected: All tests pass.

- [ ] **Step 8: Run all tests**

Run: `cd backend && python -m pytest tests/ -v -x -k "detail or surface or orchestrator or component"`
Expected: All pass.

- [ ] **Step 9: Run ruff**

Run: `cd backend && ruff check src/ui/renderer.py src/services/surface_detail_builders.py src/api/routes_surface_detail.py`
Expected: No errors.

- [ ] **Step 10: Commit**

```bash
cd backend && git add src/ui/renderer.py src/services/surface_detail_builders.py src/api/routes_surface_detail.py tests/test_detail_tabs_new.py
git commit -m "feat: add detail tabs for all 12 surface kinds (30 builders total)"
```

---

## Final Verification

- [ ] **Run full backend test suite**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All tests pass.

- [ ] **Run ruff on all modified files**

Run: `cd backend && ruff check src/ tests/`
Expected: No errors.

- [ ] **Run frontend build**

Run: `cd frontend && npm run build`
Expected: Build succeeds.

- [ ] **Final commit (if any remaining changes)**

```bash
git status
# If clean, done. If not, stage and commit remaining.
```
