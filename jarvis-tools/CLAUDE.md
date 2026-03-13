# Plugin Development Rules

This is the OpenClaw plugin for Jarvis. It is intentionally thin.

## The One Rule

**This plugin only makes HTTP calls to the Jarvis backend. No business logic. No state. No heavy computation.**

If you're tempted to add logic here, put it in the backend instead.

## Running

```bash
npm install        # Install deps
npx tsc --noEmit   # Type check
npm run build      # Compile (optional — OpenClaw loads TS via jiti)
```

## Adding a Tool

In `src/tools.ts`:

```typescript
api.registerTool({
  name: "jarvis_new_thing",
  description: "What this tool does. When the model should use it.",
  parameters: Type.Object({
    param: Type.String({ description: "What this param is" }),
  }),
  async execute(_id: string, params: { param: string }) {
    const res = await callBackend(config, "/v1/endpoint", "POST", params);
    return formatResult(res);
  },
});
```

## Adding an HTTP Route

In `src/routes.ts`:

```typescript
forwardRoute(api, "/jarvis/webhook/source", "/v1/webhooks/source", config);
```

## After Changes

1. Type-check: `npx tsc --noEmit`
2. Update `openclaw.example.json5` tools.allow if adding a new tool
3. Update `jarvis-agent/SOUL.md` with new tool capabilities
