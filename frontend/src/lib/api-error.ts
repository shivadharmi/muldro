/** Centralized parsing of the backend's standardized error envelope.
 *
 * Backend error contract (REST / SSE / WS) — `message` is always client-safe:
 *   REST: { "error": { "code", "message", "correlation_id" } }  (+ X-Request-ID header)
 *   SSE : { "event": "error", "code", "message", "correlation_id" }
 *   WS  : { "status": "error", "code", "message", "correlation_id" }
 *
 * Defense-in-depth: we NEVER render a raw response body, `detail`, or stack to
 * the user. Anything that doesn't match the envelope falls back to a generic,
 * safe message.
 */

export const SAFE_FALLBACK_MESSAGE = "Something went wrong.";

export interface ParsedApiError {
  code: string;
  message: string;
  correlationId: string | null;
}

/**
 * One rejected binding, as `PUT /v1/model-config` reports it. Mirrors
 * `ConfigWarning` in `./types` — kept structural here to avoid a circular
 * import between the error-parsing module and the domain types module.
 */
export interface BindRejection {
  scope_type: "tier" | "agent";
  scope_key: string;
  provider: string;
  code: "provider_not_configured";
  message: string;
}

/** Shape of the standardized error envelope (REST/SSE/WS share these fields). */
interface ErrorEnvelopeFields {
  code?: unknown;
  message?: unknown;
  correlation_id?: unknown;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

/**
 * Normalize an error envelope's fields into a safe ParsedApiError.
 * Used by REST (`body.error`), SSE (event payload), and WS (frame payload).
 * Always returns a client-safe message — never a raw body.
 */
function fromEnvelopeFields(fields: ErrorEnvelopeFields): ParsedApiError {
  return {
    code: asString(fields.code) ?? "error",
    message: asString(fields.message) ?? SAFE_FALLBACK_MESSAGE,
    correlationId: asString(fields.correlation_id),
  };
}

/**
 * Parse a REST error response body (already-parsed JSON or a raw string) into a
 * safe ParsedApiError. Reads `body.error.{message,code,correlation_id}`.
 *
 * NEVER returns a raw `detail`/body string as the user-facing message — if the
 * shape is unexpected, falls back to {@link SAFE_FALLBACK_MESSAGE}.
 *
 * @param body  parsed JSON object, or a raw response-body string
 * @param correlationIdHeader  value of the `X-Request-ID` header, if available
 */
export function parseApiError(
  body: unknown,
  correlationIdHeader?: string | null,
): ParsedApiError {
  let parsed: unknown = body;

  // Accept a raw string body (e.g. from res.text()) and try to JSON-parse it,
  // but never surface the raw string itself.
  if (typeof body === "string") {
    try {
      parsed = JSON.parse(body);
    } catch {
      parsed = null;
    }
  }

  const headerCid = asString(correlationIdHeader ?? null);

  if (isRecord(parsed) && isRecord(parsed.error)) {
    const result = fromEnvelopeFields(parsed.error as ErrorEnvelopeFields);
    return {
      ...result,
      correlationId: result.correlationId ?? headerCid,
    };
  }

  // Unexpected shape (legacy `{detail}`, HTML, empty, etc.) — safe fallback only.
  return {
    code: "error",
    message: SAFE_FALLBACK_MESSAGE,
    correlationId: headerCid,
  };
}

/**
 * Recognize the ONE deliberate exception to the standard error envelope:
 * `PUT /v1/model-config` returns `422 { "detail": [ConfigWarning, ...] }` when
 * a binding cannot resolve to a runnable model (see
 * `backend/src/api/routes_model_config.py::put_model_config`). That body does
 * NOT match `{ error: {...} }`, so `parseApiError` would silently collapse it
 * to the generic fallback and discard which binding failed and why.
 *
 * Returns the rejected bindings when `body` looks like `{ detail: [...] }`
 * with entries carrying at least `scope_key` and `code`, and `null` for every
 * other shape (including a malformed or partially-shaped body) so a caller can
 * fall back to {@link parseApiError} without this ever throwing.
 *
 * @param body  parsed JSON object, or a raw response-body string
 */
export function parseBindRejection(body: unknown): BindRejection[] | null {
  let parsed: unknown = body;

  if (typeof body === "string") {
    try {
      parsed = JSON.parse(body);
    } catch {
      return null;
    }
  }

  if (!isRecord(parsed) || !Array.isArray(parsed.detail) || parsed.detail.length === 0) {
    return null;
  }

  const rejections: BindRejection[] = [];
  for (const entry of parsed.detail) {
    if (
      !isRecord(entry) ||
      (entry.scope_type !== "tier" && entry.scope_type !== "agent") ||
      typeof entry.scope_key !== "string" ||
      entry.code !== "provider_not_configured"
    ) {
      return null;
    }
    rejections.push({
      scope_type: entry.scope_type,
      scope_key: entry.scope_key,
      provider: asString(entry.provider) ?? "",
      code: "provider_not_configured",
      message: asString(entry.message) ?? SAFE_FALLBACK_MESSAGE,
    });
  }
  return rejections;
}

/**
 * Parse an SSE `error` event payload ({ event:"error", code, message,
 * correlation_id }) into a safe ParsedApiError.
 */
export function parseSseError(event: ErrorEnvelopeFields): ParsedApiError {
  return fromEnvelopeFields(event);
}

/**
 * Parse a WS error frame ({ status:"error", code, message, correlation_id })
 * into a safe ParsedApiError. Never reads a legacy raw `error` field as the
 * user-facing message.
 */
export function parseWsError(frame: ErrorEnvelopeFields): ParsedApiError {
  return fromEnvelopeFields(frame);
}

/**
 * Format a ParsedApiError for display, appending the correlation id subtly so
 * users can report it. e.g. "Not found — reference: req_abc123".
 */
export function formatApiError(err: ParsedApiError): string {
  if (err.correlationId) {
    return `${err.message} — reference: ${err.correlationId}`;
  }
  return err.message;
}

/** Minimal structural shape of an ApiError (avoids a circular import). */
interface ApiErrorLike {
  safeMessage: string;
  code: string;
  correlationId: string | null;
}

function isApiErrorLike(value: unknown): value is ApiErrorLike {
  return (
    isRecord(value) &&
    typeof value.safeMessage === "string" &&
    typeof value.code === "string" &&
    "correlationId" in value
  );
}

/**
 * Convert any caught value into a client-safe display string (with correlation
 * id when present). For an ApiError, uses its safe message + correlation id; for
 * anything else, returns the generic fallback — NEVER a raw `.message`/stack.
 */
export function errorToMessage(err: unknown): string {
  if (isApiErrorLike(err)) {
    return formatApiError({
      code: err.code,
      message: err.safeMessage,
      correlationId: err.correlationId,
    });
  }
  return SAFE_FALLBACK_MESSAGE;
}
