"use client";

import { useCallback, useEffect, useState } from "react";
import { approveAction, fetchApprovals, rejectAction } from "@/lib/api";
import type { Approval } from "@/lib/types";

const PREPARED_TYPE = "prepared_action";

/**
 * The review queue for work that was STAGED and not executed.
 *
 * A prepared action is a write a gate wanted a human for on a turn with no
 * human present. CLAUDE.md: this queue is the ONLY place such an item can be
 * acted on, which is why it landed before the old detail modal's `queue` tab
 * was deleted rather than after.
 *
 * A decided row is removed from the list immediately. Confirmation replays the
 * exact recorded tool call and is exactly-once via the idempotency ledger, so
 * a second click is refused server-side — but showing a decided row as
 * outstanding is a bug in its own right, and this closes it at the source.
 */
export function PreparedQueue() {
  const [rows, setRows] = useState<Approval[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchApprovals("pending", PREPARED_TYPE)
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const decide = useCallback(async (id: string, verb: "approve" | "reject") => {
    setBusy(id);
    try {
      await (verb === "approve" ? approveAction(id) : rejectAction(id));
      setRows((current) => (current ?? []).filter((r) => r.approval_id !== id));
    } catch {
      setFailed(true);
    } finally {
      setBusy(null);
    }
  }, []);

  if (failed) {
    return <p className="text-xs text-j-error">The review queue could not be loaded.</p>;
  }
  if (rows === null) {
    return <p className="text-xs text-t-muted">Loading the review queue…</p>;
  }
  if (rows.length === 0) {
    return <p className="text-xs text-t-muted">Nothing is waiting for your decision.</p>;
  }

  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-[11px] font-medium text-t-muted uppercase tracking-wide">
        Prepared for your review
      </h3>
      <p className="text-xs text-t-tertiary">
        Nothing has run. Each of these is waiting for your decision.
      </p>
      {rows.map((row) => (
        <div
          key={row.approval_id}
          className="rounded-[var(--radius-md)] border border-b-secondary bg-surface-2 p-3 flex flex-col gap-2"
        >
          <p className="text-[13px] text-t-primary">{row.title}</p>
          {row.summary && <p className="text-xs text-t-tertiary">{row.summary}</p>}
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={busy === row.approval_id}
              onClick={() => decide(row.approval_id, "approve")}
              className="text-xs px-3 py-1.5 rounded-[var(--radius-md)] bg-j-primary text-j-primary-fg font-medium disabled:opacity-50"
            >
              Approve
            </button>
            <button
              type="button"
              disabled={busy === row.approval_id}
              onClick={() => decide(row.approval_id, "reject")}
              className="text-xs px-3 py-1.5 rounded-[var(--radius-md)] bg-surface-3 text-t-secondary disabled:opacity-50"
            >
              Reject
            </button>
            <span className="text-[10px] font-mono text-t-muted ml-auto">{row.risk_level}</span>
          </div>
        </div>
      ))}
    </section>
  );
}
