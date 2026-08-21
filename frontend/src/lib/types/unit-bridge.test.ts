import { test, expect } from "vitest";
import { unitFromSurface } from "./unit-bridge";
import type { WorkspaceSurface } from "@/stores/surface-store";

// The bridge is temporary, but it is currently the only `Frame` producer that
// reaches a screen: every card on the workspace grid and in the chat surfaces
// panel is built by this function.

function surface(overrides: Partial<WorkspaceSurface> = {}): WorkspaceSurface {
  return {
    id: "sur_01",
    kind: "run",
    preview: {
      title: "Weekly digest",
      subtitle: "Two things need you.\n\nAnd a second paragraph.",
      status: "completed",
      priority: null,
      metrics: [],
      entities: [],
      progress: null,
      timestamp: "2026-08-20T09:00:00Z",
      tags: ["gmail", "digest"],
    },
    detail_config: null,
    source_run_id: null,
    response_preview: null,
    created_at: "2026-08-19T08:00:00Z",
    ...overrides,
  } as WorkspaceSurface;
}

test("a well-formed surface maps onto a Unit", () => {
  const u = unitFromSurface(surface());

  expect(u.frame.key).toBe("sur_01");
  expect(u.frame.headline).toBe("Weekly digest");
  // The first tag is the source; the surface kind is the entity type.
  expect(u.frame.source).toBe("gmail");
  expect(u.frame.entity_type).toBe("run");
  expect(u.frame.occurred_at).toBe("2026-08-20T09:00:00Z");
  // Flat constants, not a second kind taxonomy.
  expect(u.frame.kind).toBe("record");
  expect(u.frame.status).toBe("seen");
  expect(u.body).toBe("Two things need you.\n\nAnd a second paragraph.");
  expect(u.quotes).toEqual([]);
});

test("a surface with no tags falls back to muldro as the source", () => {
  const u = unitFromSurface(surface({ preview: { ...surface().preview, tags: [] } }));

  expect(u.frame.source).toBe("muldro");
});

test("created_at is the timestamp fallback, never the epoch", () => {
  // A missing preview timestamp used to fall to `new Date(0)`, which renders
  // as "20000d ago" on the card.
  const u = unitFromSurface(surface({ preview: { ...surface().preview, timestamp: null } }));

  expect(u.frame.occurred_at).toBe("2026-08-19T08:00:00Z");
  expect(u.frame.updated_at).toBe("2026-08-19T08:00:00Z");
});

test("a surface with no preview does not throw", () => {
  // `addSurface` guards only `id` and `kind`, so a `workspace_surface_push`
  // carrying `{id, kind, created_at}` and no preview lands in the store. This
  // function runs above the per-card ErrorBoundary: a TypeError here blanks the
  // whole workspace page instead of costing one card.
  const malformed = {
    id: "sur_bad",
    kind: "alert",
    created_at: "2026-08-19T08:00:00Z",
  } as unknown as WorkspaceSurface;

  const u = unitFromSurface(malformed);

  expect(u.frame.key).toBe("sur_bad");
  expect(u.frame.headline).toBe("");
  expect(u.frame.source).toBe("muldro");
  expect(u.frame.entity_type).toBe("alert");
  expect(u.frame.occurred_at).toBe("2026-08-19T08:00:00Z");
  expect(u.body).toBe("");
});

test("a surface with a partial preview does not throw", () => {
  const partial = {
    id: "sur_partial",
    kind: "alert",
    created_at: "2026-08-19T08:00:00Z",
    preview: { title: "Half a preview" },
  } as unknown as WorkspaceSurface;

  const u = unitFromSurface(partial);

  expect(u.frame.headline).toBe("Half a preview");
  expect(u.frame.source).toBe("muldro");
  expect(u.body).toBe("");
});

test("a surface with neither preview nor created_at still yields a Unit", () => {
  // An empty timestamp renders as `--` through TimeAgo rather than throwing.
  const u = unitFromSurface({ id: "sur_min", kind: "alert" } as unknown as WorkspaceSurface);

  expect(u.frame.occurred_at).toBe("");
  expect(u.frame.updated_at).toBe("");
});
