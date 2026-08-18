import { test, expect, vi, afterEach } from "vitest";
import { streamResume, type ChatSSEEvent } from "./api";

/** A minimal Response stub whose body streams the given SSE text once. Avoids depending on
 * global Response/ReadableStream support in the test env — streamResume only touches
 * `res.ok` and `res.body.getReader().read()`. */
function mockSseResponse(frames: string): Response {
  const chunks = [new TextEncoder().encode(frames)];
  let i = 0;
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { done: false, value: chunks[i++] }
            : { done: true, value: undefined },
      }),
    },
  } as unknown as Response;
}

afterEach(() => vi.unstubAllGlobals());

test("streamResume POSTs the decision to /chat/resume and parses SSE frames", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    mockSseResponse(
      'event: message_id\ndata: {"event":"message_id","message_id":"msg_resume"}\n\n' +
        'event: response\ndata: {"event":"response","text":"Sent."}\n\n' +
        'event: done\ndata: {"event":"done"}\n\n'
    )
  );
  vi.stubGlobal("fetch", fetchMock);

  const events: ChatSSEEvent[] = [];
  await streamResume(
    "apr_1",
    "approve",
    (e) => events.push(e),
    undefined,
    "conv_1",
    "looks good"
  );

  // POST shape: routes to /chat/resume with the decision + reason + conversation.
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/muldro/chat/resume");
  expect((init as RequestInit).method).toBe("POST");
  const body = JSON.parse((init as RequestInit).body as string);
  expect(body).toMatchObject({
    approval_id: "apr_1",
    decision: "approve",
    reason: "looks good",
    conversation_id: "conv_1",
    surface: "web",
  });

  // The continuation frames are parsed and delivered in order.
  expect(events.map((e) => e.event)).toEqual(["message_id", "response", "done"]);
  expect(events.find((e) => e.event === "response")?.text).toBe("Sent.");
});

test("streamResume omits reason/conversation_id when not provided", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValue(mockSseResponse('event: done\ndata: {"event":"done"}\n\n'));
  vi.stubGlobal("fetch", fetchMock);

  await streamResume("apr_2", "reject", () => {});

  const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
  expect(body).toMatchObject({ approval_id: "apr_2", decision: "reject", surface: "web" });
  expect(body.reason).toBeUndefined();
  expect(body.conversation_id).toBeUndefined();
});
