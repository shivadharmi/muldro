"use client";

import { useId } from "react";

import type {
  AgentInfo,
  CatalogModel,
  CatalogProvider,
  ConfigWarning,
  ModelBinding,
} from "@/lib/types";
import { btn } from "../controls";
import { WarningIcon } from "../icons";
import { BindingFields, type BindingPatch } from "./binding-fields";

/** §9.3 `agent-chip` — the 19px/5px-radius chip, distinct from the 20px pill
 *  `chip` and the 18px `tchip`. Three sizes exist; do not add a fourth. */
const AGENT_CHIP =
  "inline-flex items-center h-[19px] px-[7px] rounded-[5px] text-[11px] " +
  "font-normal whitespace-nowrap shrink-0 bg-surface-3 text-t-tertiary";

/** §9.6's warning action. Composed once, at module scope, because `btn()`
 *  returns a pure function of its arguments and nothing here varies per render. */
const CONNECT_BTN = btn({ size: "md", variant: "warning" });

/**
 * `ModelSpec.thinking_style`, said out loud.
 *
 * `label` is the meta-row reading; `adjective` is the same fact used
 * attributively in §9.5's capability hint. Two forms rather than one string
 * mangled at the call site, because "Adaptive thinking" and "Adaptive-thinking
 * models…" are different grammar, not different formatting.
 */
const THINKING_STYLES: Record<string, { label: string; adjective: string }> = {
  anthropic_adaptive: { label: "Adaptive thinking", adjective: "Adaptive-thinking" },
  anthropic_legacy: { label: "Budgeted thinking", adjective: "Budgeted-thinking" },
  openai_effort: { label: "Reasoning effort", adjective: "Reasoning" },
  // Not "Provider thinking": this label RENDERS today, on both Gemini models,
  // and naming the internal enum rather than the provider tells the founder
  // nothing about the model they just picked.
  gemini: { label: "Gemini thinking", adjective: "Gemini-thinking" },
  none: { label: "No thinking", adjective: "Non-thinking" },
};

/** Drop a trailing `.0` so 200000 reads "200K", not "200.0K". */
function compact(value: number): string {
  return String(Math.round(value * 10) / 10);
}

function formatContext(tokens: number): string {
  if (tokens >= 1_000_000) return `${compact(tokens / 1_000_000)}M context`;
  if (tokens >= 1_000) return `${compact(tokens / 1_000)}K context`;
  return `${tokens} context`;
}

/** The catalog stores cost per 1k tokens; §9.5 displays it per Mtok. The ×1000
 *  is rounded because `0.005 * 1000` is not exactly 5 in binary floating point,
 *  and "$5.000000000000001 per Mtok" is not a price. */
function formatUsdPerMtok(costPerThousand: number): string {
  return `$${Math.round(costPerThousand * 1000 * 100) / 100}`;
}

/** Sentence case, per **A3** — a card must not head itself with a raw slug. */
function tierLabel(scopeKey: string): string {
  return scopeKey.charAt(0).toUpperCase() + scopeKey.slice(1);
}

/** The `·` in `text-b-strong` (§9.5) — a separator, not content, so it is
 *  hidden from the accessibility tree rather than read between every fact. */
function MetaSeparator() {
  return (
    <span aria-hidden="true" className="text-b-strong">
      ·
    </span>
  );
}

export interface TierCardProps {
  /**
   * The tier binding this card edits. Also the card's IDENTITY: the heading
   * reads `binding.scope_key` rather than a separate `tier` prop, so there is
   * no second string that could name a tier the grid below is not editing.
   */
  binding: ModelBinding;
  /** The full catalog. Passed whole, as `BindingFields` takes it, so the
   *  (provider, model_id) identity rule stays in exactly one place. */
  models: readonly CatalogModel[];
  /** Catalog providers — for the derived provider name and the Connect label. */
  providers: readonly CatalogProvider[];
  /** ALL agents, filtered to this tier here. Filtering at the call site would
   *  put the tier→agent rule in the parent once per card, i.e. three times. */
  agents: readonly AgentInfo[];
  /** The one-line description beside the tier name. */
  description: string;
  /** This binding differs from the saved one (§9.5's meta-row right slot). */
  dirty?: boolean;
  /** Whole-card disable, e.g. mid-save. */
  disabled?: boolean;
  /**
   * The server-computed `warnings` entry FOR THIS BINDING (§4.4): the bound
   * provider resolves no credential, so every agent on this tier fails at run
   * time. It is stale the moment the draft is rebound elsewhere, and the card
   * ignores it once `warning.provider` and `binding.provider` disagree — see
   * `standing` below. Passing another scope's warning shows nothing.
   */
  warning?: ConfigWarning;
  /**
   * A 422 the server returned while SAVING this binding (§4.4). Rendered on the
   * card, never as a toast — the founder has to see which binding was refused.
   * Unlike a warning it describes an attempt, not a live state: nothing is
   * failing, because the refused binding never became the running one.
   */
  rejection?: ConfigWarning;
  onChange: (patch: BindingPatch) => void;
  onOpenPicker: () => void;
  /** Switches to the Providers tab with `provider` pre-expanded. Receives the
   *  SLUG; the display name is presentation and is resolved here. */
  onConnectProvider: (provider: string) => void;
}

/**
 * One tier — `reasoning`, `balanced` or `fast` — as a card (§9.5, §9.6).
 *
 * Purely presentational: it holds no state, fetches nothing, and never decides
 * what a change means. It owns three things the grid below it cannot: which
 * agents ride on this tier, what the selected model actually costs, and — the
 * point of the redesign — what happens when the bound provider has no
 * credential.
 *
 * **The unconfigured state is stated as a consequence, not as a status.** There
 * is no tier fallback (§2.5): a tier bound to a disconnected provider fails
 * every agent on it. So the copy names the agents' fate and the card never uses
 * language that implies something else picks up the slack.
 *
 * **A rejection outranks a warning, and does not say the same thing.** They
 * share one slot because they are one card's worth of bad news, but they
 * describe opposite states: a warning means a live binding is broken and the
 * agents on this tier are failing now; a rejection means the save was refused,
 * so the previously saved binding is still running and nothing is failing.
 * A rejection is also the newer fact — showing the older one on top of a fresh
 * refusal would answer a question nobody asked.
 */
export function TierCard({
  binding,
  models,
  providers,
  agents,
  description,
  dirty = false,
  disabled = false,
  warning,
  rejection,
  onChange,
  onOpenPicker,
  onConnectProvider,
}: TierCardProps) {
  const uid = useId();
  const headingId = `${uid}-tier`;
  const consequenceId = `${uid}-consequence`;

  /**
   * A warning names the provider that failed to resolve. Once the draft names a
   * DIFFERENT provider, that warning is about a binding that no longer exists —
   * the founder has already done what the card asked, by rebinding rather than
   * by connecting. The next save either succeeds or comes back as a rejection
   * naming the new provider. Without this check the card would render an amber
   * "Claude Haiku · Anthropic" above a **Connect Groq** button.
   *
   * A rejection needs no such check: it describes the binding just attempted,
   * whose provider IS `binding.provider`.
   */
  const standing = warning?.provider === binding.provider ? warning : undefined;
  const notice = rejection ?? standing;
  const warned = notice !== undefined;

  const tierName = tierLabel(binding.scope_key);
  const tierAgents = agents.filter((agent) => agent.tier === binding.scope_key);

  // Identified by BOTH keys: a bare `model_id` is not unique across providers.
  const selectedModel = models.find(
    (m) => m.provider === binding.provider && m.model_id === binding.model_id,
  );
  const thinking = selectedModel
    ? THINKING_STYLES[selectedModel.thinking_style]
    : undefined;

  // §9.5's right slot is exclusive: the hint states a fact about the model, the
  // marker states a fact about the founder's edit, and the edit is the newer of
  // the two. The hint is derived from `accepts_temperature` rather than from the
  // thinking style, so it can never claim a capability we did not look up.
  const capabilityHint =
    selectedModel && !selectedModel.accepts_temperature
      ? `${thinking?.adjective ?? "These"} models do not accept temperature.`
      : null;

  const noticeProviderName = notice
    ? (providers.find((p) => p.provider === notice.provider)?.display_name ??
      notice.provider)
    : "";

  /**
   * The server's sentence when it sent one; otherwise ours.
   *
   * Two fallbacks, not one, because the two notices describe OPPOSITE states of
   * the world. A warning means a live binding is broken, so every agent on this
   * tier really is failing. A rejection is a 422 — the save was REFUSED, so the
   * previously saved binding is still what runs and nothing is failing at all.
   * One shared sentence would tell a founder whose rebind was rejected that
   * their agents are down. That is the same defect as promising a fallback,
   * pointed the other way: the card's one job is to not invent a consequence.
   *
   * `.trim() ||` rather than `??`: a server that sends `""` must not render an
   * empty amber row.
   */
  const consequence =
    notice?.message.trim() ||
    (rejection
      ? `${noticeProviderName} is not connected, so ${tierName} was not saved. ` +
        `Connect it first — there is no tier fallback.`
      : `${noticeProviderName} is not connected. There is no tier fallback — ` +
        `every agent on ${tierName} will fail until you connect it.`);

  return (
    <section
      aria-labelledby={headingId}
      // The consequence is part of what this card IS, not only an announcement.
      // A live region can miss its moment; a description cannot — whoever
      // reaches the card afterwards still hears why it is amber.
      aria-describedby={notice ? consequenceId : undefined}
      className={
        "bg-surface-1 border rounded-[var(--radius-lg)] pt-[13px] px-[20px] pb-[11px] " +
        (warned ? "border-j-warning/35" : "border-b-secondary")
      }
    >
      <div className="flex items-center justify-between gap-3 mb-[11px]">
        <div className="flex items-baseline gap-[10px] min-w-0">
          <h3
            id={headingId}
            className="text-[13px] font-semibold tracking-[.02em] text-t-primary shrink-0"
          >
            {tierName}
          </h3>
          <p className="text-[12px] text-t-tertiary truncate">{description}</p>
        </div>

        {/* The agents that ride on this tier. Rendered as a list so a screen
            reader announces a count rather than a run-on of display names. */}
        {tierAgents.length > 0 && (
          <ul
            aria-label={`Agents on ${tierName}`}
            className="flex items-center gap-[5px] shrink-0"
          >
            {tierAgents.map((agent) => (
              <li key={agent.name} className={AGENT_CHIP}>
                {agent.display_name}
              </li>
            ))}
          </ul>
        )}
      </div>

      <BindingFields
        binding={binding}
        models={models}
        providers={providers}
        onChange={onChange}
        onOpenPicker={onOpenPicker}
        dirty={dirty}
        disabled={disabled}
        // §9.6's Model-control substitutions — the amber border and the amber
        // provider name — are inside the grid, so they can only be asked for
        // from here. A rejection recolours it for the same reason a warning
        // does: in both cases the bound provider resolves no credential.
        warning={warned}
      />

      {/* One row, two contents, one geometry — §9.6 substitutes, it does not
          add. A warned card that grew a row would reflow the whole stack the
          moment a credential was revoked. */}
      <div
        className={
          "mt-[10px] pt-[9px] border-t flex items-center gap-[9px] " +
          (warned ? "border-j-warning/25" : "border-b-secondary")
        }
      >
        {notice ? (
          <>
            <WarningIcon size={14} className="text-j-warning" />
            {/* Keyed so a warning becoming a rejection REMOUNTS this node.
                A screen reader registers a live region when it is inserted;
                flipping `role="alert"` onto a node already in the DOM is the
                documented unreliable case — and warning→rejection (a revoked
                provider, a save, a 422) is the likeliest sequence there is. */}
            <p
              key={rejection ? "rejection" : "warning"}
              id={consequenceId}
              role={rejection ? "alert" : undefined}
              className="flex-1 min-w-0 text-[12px] text-j-warning"
            >
              {consequence}
            </p>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onConnectProvider(notice.provider)}
              className={CONNECT_BTN}
            >
              Connect {noticeProviderName}
            </button>
          </>
        ) : (
          <>
            {selectedModel ? (
              <>
                <span className="text-[11.5px] text-t-muted tabular-nums">
                  {formatContext(selectedModel.context_window)}
                </span>
                <MetaSeparator />
                <span className="text-[11.5px] text-t-muted tabular-nums">
                  {formatUsdPerMtok(selectedModel.input_cost_per_1k)} /{" "}
                  {formatUsdPerMtok(selectedModel.output_cost_per_1k)} per Mtok
                </span>
                <MetaSeparator />
                <span className="text-[11.5px] text-t-muted tabular-nums">
                  {thinking?.label ?? selectedModel.thinking_style}
                </span>
              </>
            ) : (
              // A model the catalog no longer lists has no context window and no
              // price. Saying so is honest; printing a zero would not be.
              <span className="text-[11.5px] text-t-muted">
                This model is no longer in the catalog.
              </span>
            )}

            {dirty ? (
              <span className="ml-auto flex items-center gap-[6px] shrink-0">
                <span
                  aria-hidden="true"
                  className="w-[5px] h-[5px] rounded-full bg-j-primary"
                />
                <span className="text-[11.5px] text-j-primary">
                  Changed — not saved
                </span>
              </span>
            ) : (
              capabilityHint && (
                <span className="ml-auto shrink-0 text-[11.5px] text-t-muted">
                  {capabilityHint}
                </span>
              )
            )}
          </>
        )}
      </div>
    </section>
  );
}
