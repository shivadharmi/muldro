import { test, expect, vi, afterEach } from "vitest";
import { streamChat } from "./api";

afterEach(() => vi.unstubAllGlobals());

function okStream(): Response {
  return {
    ok: true,
    body: { getReader: () => ({ read: async () => ({ done: true, value: undefined }) }) },
  } as unknown as Response;
}

test("streamChat puts permission_mode in the POST body, not mode", async () => {
  const fetchMock = vi.fn().mockResolvedValue(okStream());
  vi.stubGlobal("fetch", fetchMock);

  await streamChat("hi", () => {}, undefined, "conv_1", "bypass");

  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("/api/jarvis/chat");
  const body = JSON.parse((init as RequestInit).body as string);
  expect(body).toMatchObject({
    message: "hi",
    surface: "web",
    conversation_id: "conv_1",
    permission_mode: "bypass",
  });
  expect(body).not.toHaveProperty("mode");
});

test("streamChat omits permission_mode when not provided", async () => {
  const fetchMock = vi.fn().mockResolvedValue(okStream());
  vi.stubGlobal("fetch", fetchMock);

  await streamChat("hi", () => {});

  const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
  expect(body).not.toHaveProperty("permission_mode");
});
