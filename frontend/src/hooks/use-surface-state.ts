"use client";

import { useCallback, useState } from "react";
import type { A2UISurface } from "@/lib/a2ui-types";

/** Manages a stack of A2UI surfaces with update/replace semantics. */
export function useSurfaceState() {
  const [surfaces, setSurfaces] = useState<Map<string, A2UISurface>>(new Map());

  const upsertSurface = useCallback((surface: A2UISurface) => {
    setSurfaces((prev) => {
      const next = new Map(prev);
      next.set(surface.id, surface);
      return next;
    });
  }, []);

  const removeSurface = useCallback((surfaceId: string) => {
    setSurfaces((prev) => {
      const next = new Map(prev);
      next.delete(surfaceId);
      return next;
    });
  }, []);

  const clearAll = useCallback(() => {
    setSurfaces(new Map());
  }, []);

  return {
    surfaces: Array.from(surfaces.values()),
    upsertSurface,
    removeSurface,
    clearAll,
  };
}
