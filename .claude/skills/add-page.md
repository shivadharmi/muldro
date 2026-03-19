---
description: Add a new frontend page with components and API integration
user-invocable: true
---

# Add a new frontend page

The frontend is Next.js App Router at `frontend/src/`. Pages go in `frontend/src/app/{name}/page.tsx`.

## Steps

1. **Ask the user**: What does this page display? What API endpoints does it call?
2. **Read existing pages** for patterns:
   - `frontend/src/app/tasks/page.tsx` — CRUD list + detail
   - `frontend/src/app/briefings/page.tsx` — read-only viewer
   - `frontend/src/app/chat/page.tsx` — WebSocket chat
3. **Create the page** at `frontend/src/app/{name}/page.tsx`:
   - Use `"use client"` directive for interactive pages
   - Use React Query (`@tanstack/react-query`) for data fetching
   - Import API client from `frontend/src/lib/api.ts`
   - Use existing UI components from `frontend/src/components/ui/` (Button, Card, Badge, Modal, Table, Tabs)
4. **Create feature components** if needed at `frontend/src/components/{name}/`:
   - Keep page.tsx thin — delegate to feature components
   - Use TypeScript interfaces from `frontend/src/lib/types.ts`
5. **Add navigation** in `frontend/src/components/layout/sidebar.tsx`:
   - Add NavItem for the new page
6. **For real-time data**:
   - WebSocket: use `frontend/src/hooks/use-jarvis-ws.ts`
   - SSE: use `frontend/src/hooks/use-sse.ts`
   - Notifications: use `frontend/src/hooks/use-notifications.ts`
7. **For A2UI surfaces**: use the renderer at `frontend/src/components/a2ui/renderer.tsx`
8. **Run**: `cd frontend && npm run build && npm run lint`
