import { test, expect, describe } from "vitest";
import {
  errorToMessage,
  formatApiError,
  parseApiError,
  parseSseError,
  parseWsError,
  SAFE_FALLBACK_MESSAGE,
} from "./api-error";

describe("parseApiError (REST envelope)", () => {
  test("parses new standardized envelope", () => {
    const body = {
      error: {
        code: "not_found",
        message: "The requested resource was not found.",
        correlation_id: "req_abc123",
      },
    };
    expect(parseApiError(body)).toEqual({
      code: "not_found",
      message: "The requested resource was not found.",
      correlationId: "req_abc123",
    });
  });

  test("accepts a raw JSON string body", () => {
    const raw = JSON.stringify({
      error: { code: "bad_request", message: "Invalid input.", correlation_id: "req_x" },
    });
    expect(parseApiError(raw)).toEqual({
      code: "bad_request",
      message: "Invalid input.",
      correlationId: "req_x",
    });
  });

  test("falls back to X-Request-ID header when envelope lacks correlation_id", () => {
    const body = { error: { code: "server_error", message: "Boom." } };
    const parsed = parseApiError(body, "req_header_1");
    expect(parsed.correlationId).toBe("req_header_1");
    expect(parsed.message).toBe("Boom.");
  });

  test("envelope correlation_id wins over header", () => {
    const body = { error: { message: "x", correlation_id: "req_body" } };
    expect(parseApiError(body, "req_header").correlationId).toBe("req_body");
  });

  test("legacy {detail} shape never leaks — safe fallback", () => {
    const legacy = { detail: "Traceback: secret stack trace at line 42" };
    const parsed = parseApiError(legacy);
    expect(parsed.message).toBe(SAFE_FALLBACK_MESSAGE);
    expect(parsed.message).not.toContain("Traceback");
    expect(parsed.message).not.toContain("secret");
  });

  test("raw non-JSON string never surfaced", () => {
    const parsed = parseApiError("<html>500 Internal Server Error stacktrace</html>");
    expect(parsed.message).toBe(SAFE_FALLBACK_MESSAGE);
    expect(parsed.message).not.toContain("stacktrace");
  });

  test("missing message in envelope falls back safely", () => {
    const parsed = parseApiError({ error: { code: "weird" } });
    expect(parsed.code).toBe("weird");
    expect(parsed.message).toBe(SAFE_FALLBACK_MESSAGE);
  });

  test("null / undefined / unexpected primitives fall back safely", () => {
    expect(parseApiError(null).message).toBe(SAFE_FALLBACK_MESSAGE);
    expect(parseApiError(undefined).message).toBe(SAFE_FALLBACK_MESSAGE);
    expect(parseApiError(42).message).toBe(SAFE_FALLBACK_MESSAGE);
  });
});

describe("parseSseError", () => {
  test("reads message/code/correlation_id from event", () => {
    const event = {
      event: "error",
      code: "rate_limited",
      message: "Too many requests.",
      correlation_id: "req_sse",
    } as const;
    expect(parseSseError(event)).toEqual({
      code: "rate_limited",
      message: "Too many requests.",
      correlationId: "req_sse",
    });
  });

  test("missing fields fall back safely", () => {
    expect(parseSseError({}).message).toBe(SAFE_FALLBACK_MESSAGE);
  });
});

describe("parseWsError", () => {
  test("reads message/code/correlation_id from frame, never raw error field", () => {
    const frame = {
      status: "error",
      code: "forbidden",
      message: "Not allowed.",
      correlation_id: "req_ws",
    } as const;
    expect(parseWsError(frame)).toEqual({
      code: "forbidden",
      message: "Not allowed.",
      correlationId: "req_ws",
    });
  });

  test("legacy raw {error} field is ignored — safe fallback", () => {
    // Old contract used a raw `error` string; it must not be surfaced.
    const legacy = { status: "error", error: "raw internal stacktrace" } as Record<string, unknown>;
    const parsed = parseWsError(legacy);
    expect(parsed.message).toBe(SAFE_FALLBACK_MESSAGE);
    expect(parsed.message).not.toContain("stacktrace");
  });
});

describe("formatApiError", () => {
  test("appends correlation id subtly", () => {
    expect(
      formatApiError({ code: "x", message: "Not found.", correlationId: "req_1" })
    ).toBe("Not found. — reference: req_1");
  });

  test("omits reference when no correlation id", () => {
    expect(
      formatApiError({ code: "x", message: "Not found.", correlationId: null })
    ).toBe("Not found.");
  });
});

describe("errorToMessage", () => {
  test("uses safe fields from an ApiError-like object", () => {
    const apiErrorLike = {
      safeMessage: "Forbidden.",
      code: "forbidden",
      correlationId: "req_9",
    };
    expect(errorToMessage(apiErrorLike)).toBe("Forbidden. — reference: req_9");
  });

  test("plain Error never leaks its message", () => {
    const err = new Error("DB connection string postgres://user:pw@host");
    const msg = errorToMessage(err);
    expect(msg).toBe(SAFE_FALLBACK_MESSAGE);
    expect(msg).not.toContain("postgres");
  });

  test("unknown values fall back safely", () => {
    expect(errorToMessage("string error")).toBe(SAFE_FALLBACK_MESSAGE);
    expect(errorToMessage(null)).toBe(SAFE_FALLBACK_MESSAGE);
  });
});
