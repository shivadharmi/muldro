import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach, afterEach } from "vitest";

const { addToast } = vi.hoisted(() => ({ addToast: vi.fn() }));
vi.mock("@/components/ui/toast", () => ({ useToast: () => ({ addToast }) }));
vi.mock("@/lib/api", () => ({ getAuthUrl: vi.fn() }));

import { OnboardingCard } from "./onboarding-card";
import { getAuthUrl } from "@/lib/api";

const mockedGetAuthUrl = vi.mocked(getAuthUrl);

// jsdom does not allow redefining window.location.assign via spyOn because
// the property is non-configurable. Stub the entire location object so that
// assign is a plain vi.fn() that can be inspected and reset.
const assignMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("location", { assign: assignMock });
});

afterEach(() => {
  vi.unstubAllGlobals();
  assignMock.mockReset();
  mockedGetAuthUrl.mockReset();
  addToast.mockReset();
});

test("renders the three primary sources", () => {
  render(<OnboardingCard />);
  expect(screen.getByRole("button", { name: /google/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /github/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /notion/i })).toBeInTheDocument();
});

test("clicking a source connects via getAuthUrl and redirects", async () => {
  mockedGetAuthUrl.mockResolvedValue({ url: "https://oauth.example/start", provider: "github" });
  render(<OnboardingCard />);
  await userEvent.click(screen.getByRole("button", { name: /github/i }));
  expect(mockedGetAuthUrl).toHaveBeenCalledWith("github");
  expect(assignMock).toHaveBeenCalledWith("https://oauth.example/start");
});

test("a failed getAuthUrl shows an error toast and does not redirect", async () => {
  mockedGetAuthUrl.mockRejectedValue(new Error("boom"));
  render(<OnboardingCard />);
  await userEvent.click(screen.getByRole("button", { name: /google/i }));
  expect(assignMock).not.toHaveBeenCalled();
  expect(addToast).toHaveBeenCalledWith(expect.stringMatching(/couldn't start connecting/i), "error");
});

test("links to all integrations", () => {
  render(<OnboardingCard />);
  expect(screen.getByRole("link", { name: /see all integrations/i })).toHaveAttribute(
    "href",
    "/integrations",
  );
});

test("disables the other sources while one is connecting", async () => {
  // A promise that never resolves keeps the component in the "connecting" state.
  mockedGetAuthUrl.mockReturnValue(
    new Promise<{ url: string; provider: string }>(() => {}),
  );
  render(<OnboardingCard />);
  await userEvent.click(screen.getByRole("button", { name: /github/i }));
  expect(screen.getByRole("button", { name: /google/i })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: /google/i }));
  expect(mockedGetAuthUrl).toHaveBeenCalledTimes(1);
  expect(mockedGetAuthUrl).toHaveBeenCalledWith("github");
});
