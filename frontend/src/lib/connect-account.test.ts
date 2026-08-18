import { test, expect, vi } from "vitest";
import { pollUntilActive } from "./connect-account";

test("resolves active as soon as confirm returns active", async () => {
  const confirm = vi
    .fn()
    .mockResolvedValueOnce({ status: "pending" })
    .mockResolvedValueOnce({ status: "active" });
  const sleep = vi.fn().mockResolvedValue(undefined);

  const result = await pollUntilActive(confirm, {
    intervalMs: 2000,
    timeoutMs: 60000,
    sleep,
    elapsed: (() => {
      let t = 0;
      return () => (t += 2000);
    })(),
  });

  expect(result).toBe("active");
  expect(confirm).toHaveBeenCalledTimes(2);
});

test("returns timeout when never active before the ceiling", async () => {
  const confirm = vi.fn().mockResolvedValue({ status: "pending" });
  const sleep = vi.fn().mockResolvedValue(undefined);
  let t = 0;

  const result = await pollUntilActive(confirm, {
    intervalMs: 2000,
    timeoutMs: 6000,
    sleep,
    elapsed: () => (t += 2000),
  });

  expect(result).toBe("timeout");
});

test("stops early when shouldStop() becomes true (popup closed)", async () => {
  const confirm = vi.fn().mockResolvedValue({ status: "pending" });
  const sleep = vi.fn().mockResolvedValue(undefined);
  let calls = 0;

  const result = await pollUntilActive(confirm, {
    intervalMs: 2000,
    timeoutMs: 60000,
    sleep,
    elapsed: () => 0,
    shouldStop: () => ++calls >= 2,
  });

  expect(result).toBe("cancelled");
});

test("confirms once more when the popup closes, so an approval is not lost", async () => {
  // The popup closed because the user just approved: the last poll still said
  // pending, but the re-confirm activates the connection.
  const confirm = vi
    .fn()
    .mockResolvedValueOnce({ status: "pending" })
    .mockResolvedValueOnce({ status: "active" });
  const sleep = vi.fn().mockResolvedValue(undefined);
  let calls = 0;

  const result = await pollUntilActive(confirm, {
    intervalMs: 2000,
    timeoutMs: 60000,
    sleep,
    elapsed: () => 0,
    shouldStop: () => ++calls >= 2,
  });

  expect(result).toBe("active");
  expect(confirm).toHaveBeenCalledTimes(2);
});
