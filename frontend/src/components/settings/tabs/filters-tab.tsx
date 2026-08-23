"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, CardBody } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { fetchFilterRules, revokeFilterRule } from "@/lib/api";
import { errorToMessage } from "@/lib/api-error";
import type { FilterRule } from "@/lib/types";

function formatDay(iso: string | null): string {
  if (!iso) return "an unknown date";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "an unknown date";
  return parsed.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** "12 messages", "1 message" — the count is the point, so it leads. */
function releasedPhrase(released: number): string {
  return `${released} ${released === 1 ? "message" : "messages"}`;
}

function FilterRuleRow({
  rule,
  onRevoke,
  busy,
}: {
  rule: FilterRule;
  onRevoke: (ruleId: string) => void;
  busy: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-[var(--radius-lg)] border border-b-secondary bg-surface-1 px-4 py-3">
      <div className="min-w-0">
        <p className="font-mono text-[13px] text-t-primary break-all">
          {rule.match_value}
        </p>
        <p className="text-xs text-t-muted mt-1">
          {rule.source} · {rule.match_kind} · added {formatDay(rule.created_at)}
          {rule.enabled ? "" : ` · revoked ${formatDay(rule.revoked_at)}`}
        </p>
      </div>
      {rule.enabled && (
        <button
          type="button"
          onClick={() => onRevoke(rule.rule_id)}
          disabled={busy}
          aria-busy={busy}
          aria-label={`Revoke filter for ${rule.match_value}`}
          className="shrink-0 rounded-[var(--radius-md)] border border-b-secondary px-2.5 py-1 text-xs text-t-secondary transition-colors cursor-pointer hover:text-t-primary hover:bg-surface-2 disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-j-ring"
        >
          {busy ? "Revoking…" : "Revoke"}
        </button>
      )}
    </div>
  );
}

function RuleSection({
  id,
  title,
  rules,
  onRevoke,
  revoking,
}: {
  id: string;
  title: string;
  rules: FilterRule[];
  onRevoke: (ruleId: string) => void;
  revoking: string | null;
}) {
  return (
    <section aria-labelledby={id}>
      <h3
        id={id}
        className="text-[11px] uppercase text-t-muted font-medium mb-2.5 tracking-wider"
      >
        {title}
      </h3>
      <div className="space-y-2">
        {rules.map((rule) => (
          <FilterRuleRow
            key={rule.rule_id}
            rule={rule}
            onRevoke={onRevoke}
            busy={revoking === rule.rule_id}
          />
        ))}
      </div>
    </section>
  );
}

/**
 * Every mail filter the founder granted, and the button that takes one back.
 *
 * Self-contained: it owns its fetch and its revoke state, because nothing else
 * in Settings shares them. The list arrives newest-first from the server, so
 * partitioning into live and revoked preserves that order without re-sorting.
 */
export function FiltersTab() {
  const { addToast } = useToast();
  const [rules, setRules] = useState<FilterRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [revoking, setRevoking] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchFilterRules()
      .then((res) => {
        if (cancelled) return;
        setRules(res.rules);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadFailed(true);
        addToast(errorToMessage(err), "error");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [addToast]);

  const handleRevoke = useCallback(
    async (ruleId: string) => {
      setRevoking(ruleId);
      try {
        const result = await revokeFilterRule(ruleId);
        const revokedAt = new Date().toISOString();
        setRules((prev) =>
          prev.map((r) =>
            r.rule_id === ruleId
              ? { ...r, enabled: false, revoked_at: revokedAt }
              : r,
          ),
        );
        // Revoking releases the mail this rule had been holding out of view.
        // That return is the consequence the founder is actually deciding
        // about, so it is reported instead of a bare success.
        addToast(
          result.released > 0
            ? `Filter removed — ${releasedPhrase(result.released)} returned to your feed`
            : "Filter removed",
          "success",
        );
      } catch (err) {
        addToast(errorToMessage(err), "error");
      } finally {
        setRevoking(null);
      }
    },
    [addToast],
  );

  const active = rules.filter((r) => r.enabled);
  const revoked = rules.filter((r) => !r.enabled);

  return (
    <div className="space-y-6">
      <p className="text-sm text-t-tertiary leading-relaxed">
        These are senders Muldro keeps out of your feed. Muldro proposed each one
        and you confirmed it — it cannot create a filter on its own. Revoke any of
        them here and the mail they were holding comes back.
      </p>

      {loading && (
        <div className="space-y-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 rounded-[var(--radius-lg)] skeleton" />
          ))}
        </div>
      )}

      {!loading && loadFailed && (
        <Card variant="error">
          <CardBody>
            <p className="text-sm text-t-secondary">
              Your filters could not be loaded. Reopen this tab to try again.
            </p>
          </CardBody>
        </Card>
      )}

      {!loading && !loadFailed && rules.length === 0 && (
        <Card>
          <CardBody>
            <div className="text-center py-4">
              <p className="text-sm text-t-secondary font-medium mb-1">
                No filters yet
              </p>
              <p className="text-xs text-t-muted">
                Muldro has not proposed any. When it notices a sender you keep
                setting aside, it will ask before filtering anything.
              </p>
            </div>
          </CardBody>
        </Card>
      )}

      {active.length > 0 && (
        <RuleSection
          id="filters-active"
          title="Active"
          rules={active}
          onRevoke={handleRevoke}
          revoking={revoking}
        />
      )}

      {revoked.length > 0 && (
        <RuleSection
          id="filters-revoked"
          title="Revoked"
          rules={revoked}
          onRevoke={handleRevoke}
          revoking={revoking}
        />
      )}
    </div>
  );
}
