import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "@/components/ui/toast";
import type { UnifiedIntegration } from "@/lib/api";

// The page and the connect hook both reach the API module directly (no DI).
const { fetchMock, beginMock, confirmMock, authUrlMock, disconnectMock } =
  vi.hoisted(() => ({
    fetchMock: vi.fn(),
    beginMock: vi.fn(),
    confirmMock: vi.fn(),
    authUrlMock: vi.fn(),
    disconnectMock: vi.fn(),
  }));

vi.mock("@/lib/api", () => ({
  fetchUnifiedIntegrations: fetchMock,
  getAuthUrl: authUrlMock,
  disconnectInstallation: disconnectMock,
  beginConnection: beginMock,
  confirmConnection: confirmMock,
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

import IntegrationsPage from "./page";

type Popup = { closed: boolean; close: () => void };

const openMock = vi.fn();

function makePopup(closed = false): Popup {
  const popup: Popup = {
    closed,
    close: () => {
      popup.closed = true;
    },
  };
  return popup;
}

function gateway(
  overrides: Partial<UnifiedIntegration> = {},
): UnifiedIntegration {
  return {
    server_name: "google-workspace",
    display_name: "Google Workspace",
    category: "oauth",
    provider: null,
    configured: true,
    connected: false,
    health_status: "healthy",
    scopes: [],
    install_id: null,
    oc_providers: ["gmail", "googlecalendar"],
    oc_provider_labels: { gmail: "Gmail", googlecalendar: "Google Calendar" },
    provider_connections: { gmail: false, googlecalendar: false },
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <IntegrationsPage />
      </ToastProvider>
    </QueryClientProvider>,
  );
}

/** The card whose heading is `name`, so sibling cards can't satisfy a query. */
async function card(name: string): Promise<HTMLElement> {
  const heading = await screen.findByText(name);
  return heading.closest("div.rounded-\\[var\\(--radius-lg\\)\\]") as HTMLElement;
}

beforeEach(() => {
  fetchMock.mockReset();
  beginMock.mockReset();
  confirmMock.mockReset();
  authUrlMock.mockReset();
  disconnectMock.mockReset();
  openMock.mockReset();
  openMock.mockImplementation(() => makePopup());
  vi.stubGlobal("open", openMock);
  beginMock.mockImplementation(async (provider: string) => ({
    authorization_url: `https://oc.test/${provider}`,
  }));
  confirmMock.mockResolvedValue({ status: "active" });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

test("a pending gateway card waits for approval instead of claiming a redirect", async () => {
  fetchMock.mockResolvedValue([gateway()]);
  // Hold the walk open so the card stays in its pending state.
  beginMock.mockReturnValue(new Promise(() => {}));

  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Connect" }));

  // The popup-poll flow never navigates this tab, so it must never say so.
  expect(
    await screen.findByRole("button", { name: "Waiting for approval…" }),
  ).toBeDisabled();
  expect(screen.queryByText("Redirecting...")).not.toBeInTheDocument();
});

test("a second gateway card can be connected once the first run finishes", async () => {
  fetchMock.mockResolvedValue([
    gateway(),
    gateway({
      server_name: "github",
      display_name: "GitHub",
      oc_providers: ["github"],
      oc_provider_labels: { github: "GitHub" },
      provider_connections: { github: false },
    }),
  ]);

  const user = userEvent.setup();
  renderPage();

  const google = await card("Google Workspace");
  await user.click(within(google).getByRole("button", { name: "Connect" }));
  await screen.findByText("Connected successfully");

  const github = await card("GitHub");
  await user.click(within(github).getByRole("button", { name: "Connect" }));

  // The page-level pending reset released the second card's Connect button.
  await waitFor(() =>
    expect(beginMock.mock.calls.map((c) => c[0])).toEqual([
      "gmail",
      "googlecalendar",
      "github",
    ]),
  );
});

test("connecting a half-connected installation only walks the missing provider", async () => {
  // `connected` is all-of, so this still renders Connect — but re-consenting
  // Gmail would make the user dismiss a redundant popup before Calendar's.
  fetchMock.mockResolvedValue([
    gateway({
      connected: false,
      provider_connections: { gmail: true, googlecalendar: false },
    }),
  ]);

  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Connect" }));

  await waitFor(() =>
    expect(beginMock.mock.calls.map((c) => c[0])).toEqual(["googlecalendar"]),
  );
});

test("reauthorizing a connected installation re-consents every provider", async () => {
  fetchMock.mockResolvedValue([
    gateway({
      connected: true,
      install_id: null,
      provider_connections: { gmail: true, googlecalendar: true },
    }),
  ]);

  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Reauthorize" }));

  await waitFor(() =>
    expect(beginMock.mock.calls.map((c) => c[0])).toEqual([
      "gmail",
      "googlecalendar",
    ]),
  );
});

test("a blocked popup offers a click that re-enters the walk", async () => {
  fetchMock.mockResolvedValue([gateway()]);
  // Gmail's popup spends the click's user activation; calendar's is refused.
  openMock
    .mockImplementationOnce(() => makePopup())
    .mockImplementationOnce(() => null);

  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Connect" }));

  // A toast alone would be useless — the whole problem is the missing gesture.
  const retry = await screen.findByRole("button", {
    name: "Popup blocked — click to connect Google Calendar",
  });

  openMock.mockImplementation(() => makePopup());
  beginMock.mockClear();
  await user.click(retry);

  await waitFor(() =>
    expect(beginMock.mock.calls.map((c) => c[0])).toEqual(["googlecalendar"]),
  );
});

test("provider labels come from the registry, and an unmapped slug shows raw", async () => {
  fetchMock.mockResolvedValue([
    gateway({
      oc_providers: ["gmail", "googledrive"],
      oc_provider_labels: { gmail: "Gmail" },
      provider_connections: { gmail: true, googledrive: false },
    }),
  ]);

  renderPage();

  expect(await screen.findByText(/Gmail/)).toBeInTheDocument();
  // No client-side restatement of the registry: unknown slugs degrade visibly.
  expect(screen.getByText(/googledrive/)).toBeInTheDocument();
});

test("a failed connect surfaces the backend's reason instead of a bland error", async () => {
  fetchMock.mockResolvedValue([
    gateway({ oc_providers: ["gmail"], provider_connections: { gmail: false } }),
  ]);
  beginMock.mockRejectedValue({
    safeMessage: "connection service not configured",
    code: "service_unavailable",
    correlationId: null,
  });

  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Connect" }));

  expect(
    await screen.findByText(/connection service not configured/),
  ).toBeInTheDocument();
});

test("a partial run names what happened to each provider, not just 'pending'", async () => {
  fetchMock.mockResolvedValue([gateway()]);
  // Gmail connects; the user closes the calendar consent popup.
  openMock
    .mockImplementationOnce(() => makePopup())
    .mockImplementationOnce(() => makePopup(true));
  confirmMock.mockImplementation(async (provider: string) => ({
    status: provider === "gmail" ? "active" : "pending",
  }));

  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: "Connect" }));

  expect(
    await screen.findByText("Partly connected — Google Calendar cancelled"),
  ).toBeInTheDocument();
});

test("a cancelled run still refetches, so a late activation becomes visible", async () => {
  fetchMock.mockResolvedValue([
    gateway({ oc_providers: ["gmail"], provider_connections: { gmail: false } }),
  ]);
  openMock.mockImplementation(() => makePopup(true));
  confirmMock.mockResolvedValue({ status: "pending" });

  const user = userEvent.setup();
  renderPage();

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
  await user.click(await screen.findByRole("button", { name: "Connect" }));

  // refetchOnWindowFocus is off; without this invalidate nothing would ever
  // correct a card that silently reads "Not connected".
  await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThan(1));
});

/**
 * GitHub-shaped: gateway-backed for its ACTIONS and holding its own OAuth token
 * for the notifications poll. The backend derives the shape from its registries;
 * the card only renders what it is told, so the fixture states it directly.
 */
function dual(overrides: Partial<UnifiedIntegration> = {}): UnifiedIntegration {
  return gateway({
    server_name: "github",
    display_name: "GitHub",
    oc_providers: ["github"],
    oc_provider_labels: { github: "GitHub" },
    provider_connections: { github: true },
    native_provider: "github",
    native_purpose: "notifications",
    native_connected: false,
    ...overrides,
  });
}

test("a missing second credential is shown, and offered its own connect", async () => {
  // The gateway side is linked, so a single all-of "Not connected" tells the
  // founder nothing about WHICH grant is missing — and the popup flow could
  // never mint the token the poll needs.
  fetchMock.mockResolvedValue([dual()]);

  renderPage();

  expect(await screen.findByText("○ notifications")).toBeInTheDocument();
  expect(screen.getByText("✓ GitHub")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Connect notifications" }),
  ).toBeInTheDocument();
});

test("the native connect button redirects instead of opening the popup", async () => {
  fetchMock.mockResolvedValue([dual()]);
  authUrlMock.mockResolvedValue({ url: "https://github.test/authorize" });
  // jsdom refuses to navigate, and an unstubbed assign() only logs about it.
  const assign = vi.fn();
  vi.stubGlobal("location", { assign });

  const user = userEvent.setup();
  renderPage();

  await user.click(
    await screen.findByRole("button", { name: "Connect notifications" }),
  );

  await waitFor(() => expect(authUrlMock).toHaveBeenCalledWith("github"));
  expect(assign).toHaveBeenCalledWith("https://github.test/authorize");
  // Chaining the two flows would navigate this tab and abandon the popup, so
  // the native button must never enter the gateway walk.
  expect(beginMock).not.toHaveBeenCalled();
});

test("the gateway connect button opens the popup and never redirects", async () => {
  fetchMock.mockResolvedValue([
    dual({ provider_connections: { github: false }, native_connected: true }),
  ]);

  const user = userEvent.setup();
  renderPage();

  await user.click(
    await screen.findByRole("button", { name: "Connect actions" }),
  );

  await waitFor(() =>
    expect(beginMock.mock.calls.map((c) => c[0])).toEqual(["github"]),
  );
  // The popup needs the user-activation budget of the click that opened it;
  // spending it on a full-page redirect first loses the popup silently.
  expect(authUrlMock).not.toHaveBeenCalled();
});

test("a fully connected dual-credential card offers neither extra button", async () => {
  fetchMock.mockResolvedValue([
    dual({ connected: true, native_connected: true, install_id: "inst_gh" }),
  ]);

  renderPage();

  expect(await screen.findByRole("button", { name: "Reauthorize" })).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Connect notifications" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Connect actions" }),
  ).not.toBeInTheDocument();
});

test("a single-credential gateway card is untouched by the second credential", async () => {
  fetchMock.mockResolvedValue([gateway()]);

  renderPage();

  expect(await screen.findByRole("button", { name: "Connect" })).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /Connect notifications/ }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(/notifications/)).not.toBeInTheDocument();
});
