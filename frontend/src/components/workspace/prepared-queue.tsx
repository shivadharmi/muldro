"use client";

import { useCallback, useEffect, useState } from "react";
import { approveAction, fetchApprovals, rejectAction } from "@/lib/api";
import type { Approval } from "@/lib/types";

/**
 * A human label per approval type. The queue held five types and rendered them
 * identically, so a reviewer could not tell "this will replay a recorded write"
 * from "this will unpause a stalled run". The type is already on the wire — only
 * the markup ignored it.
 *
 * `step:{capability}` and a bare capability are shown as the capability itself,
 * which is the useful half. An unknown type falls back to its own string rather
 * than to a guess: a label nobody recognises is better than a wrong one.
 */
function kindLabel(approvalType: string): string {
  if (approvalType === "prepared_action") return "Prepared write";
  if (approvalType === "filter_proposal") return "Filter proposal";
  if (approvalType.startsWith("tool:")) return approvalType.slice(5);
  if (approvalType.startsWith("step:")) return approvalType.slice(5);
  return approvalType;
}

/**
 * The standing review queue: everything waiting on a decision.
 *
 * It used to ask for `prepared_action` alone while five types existed, so a
 * filter proposal, a step approval and the Governor's plan-level rows were
 * written and rendered nowhere. A queue nobody renders looks exactly like a
 * queue with nothing in it, which is why that went unnoticed.
 *
 * Chat approvals are excluded by the server, not here: they carry
 * `decision_route === "chat"`, resume a suspended turn via /chat/resume, and
 * are 409'd by these endpoints on purpose. They are answered inline in the
 * conversation that raised them, where the context to answer them lives. The
 * guard below is belt-and-braces for a server that sends one anyway.
 *
 * A decided row is removed immediately. Confirmation is exactly-once
 * server-side, but showing a decided row as outstanding is a bug in itself.
 */
export function PreparedQueue() {
  const [rows, setRows] = useState<Approval[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  // Per row. One shared flag meant a single failed reject replaced the ENTIRE
  // queue with "could not be loaded" — wrong message, wrong scope, and
  // unrecoverable without a remount.
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    fetchApprovals("pending")
      .then((data) => {
        // A row this surface cannot decide must not offer a button that 409s.
        if (!cancelled) setRows(data.filter((r) => r.decision_route !== "chat"));
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
    setErrors((e) => Object.fromEntries(Object.entries(e).filter(([k]) => k !== id)));
    try {
      await (verb === "approve" ? approveAction(id) : rejectAction(id));
      setRows((current) => (current ?? []).filter((r) => r.approval_id !== id));
    } catch (err) {
      setErrors((e) => ({
        ...e,
        [id]: err instanceof Error ? err.message : "That decision could not be recorded.",
      }));
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
        Waiting for you
      </h3>
      {/* The old copy said "Nothing has run" of every row. That is true of a
          prepared write and false of a step approval, whose run is already
          underway. It is now per row, where it can be true. */}
      {rows.map((row) => (
        <div
          key={row.approval_id}
          className="rounded-[var(--radius-md)] border border-b-secondary bg-surface-2 p-3 flex flex-col gap-2"
        >
          <div className="flex items-center gap-2">
            <span className="text-[10px] px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-surface-3 text-t-muted font-medium">
              {kindLabel(row.approval_type)}
            </span>
            {row.approval_type === "prepared_action" && (
              <span className="text-[10px] text-t-muted">nothing has run yet</span>
            )}
          </div>
          <p className="text-[13px] text-t-primary">{row.title}</p>
          {row.summary && <p className="text-xs text-t-tertiary">{row.summary}</p>}
          {errors[row.approval_id] && (
            <p className="text-xs text-j-error">{errors[row.approval_id]}</p>
          )}
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
