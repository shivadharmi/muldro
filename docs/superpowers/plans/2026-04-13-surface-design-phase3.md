# Surface Design Phase 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the 4 A2UI surface components with improved typography hierarchy, information flow, visual affordances, and interaction clarity.

**Architecture:** Structural UX fixes only — JSX reordering, class string changes, minor state additions. No backend API changes, no new component APIs, no contract changes.

**Tech Stack:** React 19, Next.js 16, TypeScript 5, Tailwind CSS 4

**Spec:** `docs/superpowers/specs/2026-04-13-surface-design-phase3-design.md`

**Parallelization:** All 4 tasks touch different files and can run in parallel. Task 5 is verification.

**Note on ApprovalContext type:** The `ApprovalContext` interface in `a2ui-types.ts` has only: `approval_id`, `step_description`, `risk_reasoning`, `trust_context`, `graduation_hint`. It does NOT have `risk_level` or `trust_level` fields. Risk color coding will use warning color as default. Trust will be styled text, not a colored badge.

---

### Task 1: Step List — Highlight + Expand/Collapse + Spacing + Compact Progress

**Files:**
- Modify: `frontend/src/components/a2ui/components/step-list.tsx`

- [ ] **Step 1: Rewrite the StepList component**

Read the file. Replace the ENTIRE `StepList` function (not `StepListCompact`) with this version that adds active step highlight, expand/collapse, and improved spacing:

```typescript
export function StepList({ steps, currentStep }: StepListProps) {
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  const toggleExpand = (stepId: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepId)) next.delete(stepId);
      else next.add(stepId);
      return next;
    });
  };

  return (
    <div className="space-y-1.5">
      {steps.map((step) => {
        const isCurrent = step.step_id === currentStep;
        const { icon, className } = statusIcon[step.status] ?? statusIcon.pending;
        const isExpanded = expandedSteps.has(step.step_id);
        const hasLongOutput = (step.output_summary?.length ?? 0) > 120;

        return (
          <div
            key={step.step_id}
            className={`flex items-start gap-2 text-sm ${
              isCurrent
                ? "bg-j-primary-soft border-l-2 border-l-j-primary py-2 px-3 rounded-[var(--radius-sm)]"
                : "py-1.5 px-2"
            }`}
          >
            <span className={`shrink-0 w-5 text-center ${className}`}>{icon}</span>
            <div className="flex-1 min-w-0">
              <span className={isCurrent ? "text-t-primary font-medium" : "text-t-secondary"}>
                {step.description}
              </span>
              {step.output_summary && step.status === "completed" && (
                <div className="mt-0.5">
                  <p className={`text-xs text-t-tertiary ${!isExpanded && hasLongOutput ? "line-clamp-2" : ""}`}>
                    {step.output_summary}
                  </p>
                  {hasLongOutput && (
                    <button
                      type="button"
                      onClick={() => toggleExpand(step.step_id)}
                      className="text-[11px] text-j-primary cursor-pointer hover:underline mt-0.5"
                    >
                      {isExpanded ? "Show less" : "Show more"}
                    </button>
                  )}
                </div>
              )}
              {step.status === "failed" && step.output_summary && (
                <div className="mt-0.5">
                  <p className={`text-xs text-j-error ${!isExpanded && hasLongOutput ? "line-clamp-2" : ""}`}>
                    {step.output_summary}
                  </p>
                  {hasLongOutput && (
                    <button
                      type="button"
                      onClick={() => toggleExpand(step.step_id)}
                      className="text-[11px] text-j-primary cursor-pointer hover:underline mt-0.5"
                    >
                      {isExpanded ? "Show less" : "Show more"}
                    </button>
                  )}
                </div>
              )}
            </div>
            {step.duration_ms != null && step.status === "completed" && (
              <span className="text-[10px] text-t-tertiary shrink-0">
                {formatDuration(step.duration_ms)}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
```

Add `useState` to the import at the top:
```typescript
import { useState } from "react";
```

- [ ] **Step 2: Update StepListCompact with progress bar**

Replace the `StepListCompact` function:

```typescript
export function StepListCompact({ steps }: { steps: StepState[] }) {
  const completed = steps.filter((s) => s.status === "completed").length;
  const failed = steps.filter((s) => s.status === "failed").length;
  const total = steps.length;

  return (
    <div className="flex items-center gap-2 text-xs text-t-tertiary">
      <span>{completed}/{total} steps</span>
      {failed > 0 && <span className="text-j-error font-medium">{failed} failed</span>}
      {total > 0 && (
        <div className="w-12 h-1 bg-surface-3 rounded-full overflow-hidden ml-1">
          <div
            className={`h-full rounded-full transition-all duration-300 ${failed > 0 ? "bg-j-error" : "bg-j-success"}`}
            style={{ width: `${(completed / total) * 100}%` }}
          />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Build and verify**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`

- [ ] **Step 4: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/src/components/a2ui/components/step-list.tsx && git commit -m "feat: improve step list with active highlight, expand/collapse output, and compact progress bar"
```

---

### Task 2: Execution Surface — Typography + Results + Failure + Spinner

**Files:**
- Modify: `frontend/src/components/a2ui/components/execution-surface.tsx`

- [ ] **Step 1: Rewrite the A2UIExecutionSurface JSX**

Read the file. Replace the ENTIRE return JSX (the `<div className="space-y-3">` through closing `</div>`) with this updated version:

```typescript
  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-t-primary">{goal}</h3>
        <span className={`text-xs font-medium ${phaseClass}`}>{labelText}</span>
      </div>

      {/* Planning spinner */}
      {phase === "planning" && (
        <div className="flex flex-col items-center gap-2 py-6">
          <div className="w-4 h-4 border-2 border-j-info/30 border-t-j-info rounded-full animate-spin" />
          <span className="text-xs text-t-tertiary">Analyzing and building plan...</span>
          <span className="text-[11px] text-t-muted">This usually takes a few seconds</span>
        </div>
      )}

      {/* Step list (shown for all phases except planning) */}
      {phase !== "planning" && steps.length > 0 && (
        <StepList steps={steps} currentStep={currentStep} />
      )}

      {/* Inline approval card */}
      {phase === "approval_needed" && approval && (
        <InlineApprovalCard approval={approval} />
      )}

      {/* Results summary */}
      {phase === "completed" && results && (
        <div className="rounded-[var(--radius-lg)] bg-j-success-soft border border-j-success/20 p-4">
          {results.key_findings.length > 0 && (
            <div>
              <p className="text-[11px] font-semibold text-t-muted uppercase tracking-wider mb-2">Key Findings</p>
              <ul className="space-y-1">
                {results.key_findings.map((f, i) => (
                  <li key={i} className="text-xs text-t-tertiary flex items-start gap-1.5">
                    <span className="text-j-success shrink-0">-</span>
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {results.artifacts_created.length > 0 && (
            <div className={results.key_findings.length > 0 ? "border-t border-b-secondary pt-3 mt-3" : ""}>
              <p className="text-[11px] font-semibold text-t-muted uppercase tracking-wider mb-2">Artifacts</p>
              <div className="flex flex-wrap gap-1.5">
                {results.artifacts_created.map((a, i) => (
                  <span key={i} className="text-[11px] px-2 py-1 rounded-[var(--radius-md)] bg-surface-2 text-t-secondary">
                    {a}
                  </span>
                ))}
              </div>
            </div>
          )}
          {results.suggested_next.length > 0 && (
            <div className={results.key_findings.length > 0 || results.artifacts_created.length > 0 ? "border-t border-b-secondary pt-3 mt-3" : ""}>
              <p className="text-[11px] font-semibold text-t-muted uppercase tracking-wider mb-2">Suggested Next</p>
              <ul className="space-y-1">
                {results.suggested_next.map((s, i) => (
                  <li key={i} className="text-xs text-t-tertiary flex items-start gap-1.5">
                    <span className="text-j-info shrink-0">&rarr;</span>
                    {s}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Failure context — show full step list for context, then error box */}
      {phase === "failed" && (
        <>
          {steps.length > 0 && (
            <StepList steps={steps} currentStep={currentStep} />
          )}
          <div className="rounded-[var(--radius-lg)] bg-j-error-soft border border-j-error/20 p-4">
            <p className="text-sm font-semibold text-j-error mb-2">Execution Failed</p>
            {steps.filter((s) => s.status === "failed").map((s) => (
              <p key={s.step_id} className="text-xs text-t-secondary">
                <span className="text-j-error">&#10007;</span> {s.description}
                {s.output_summary && `: ${s.output_summary}`}
              </p>
            ))}
          </div>
        </>
      )}

      {/* Progress bar */}
      {totalCount > 0 && (
        <div className="space-y-1">
          <div className="w-full h-1.5 bg-surface-2 rounded-full">
            <div
              className={`h-full rounded-full transition-all ${
                phase === "failed" ? "bg-j-error" : phase === "completed" ? "bg-j-success" : "bg-j-info"
              }`}
              style={{ width: `${Math.min(progressPct * 100, 100)}%` }}
            />
          </div>
          {progress && (
            <p className="text-[11px] text-t-tertiary">{progress}</p>
          )}
        </div>
      )}
    </div>
  );
```

Key changes from current:
- `space-y-3` → `space-y-4`
- Goal: `font-medium` → `font-semibold`
- Spinner: `py-4` → `py-6`, added secondary text
- Results: `p-3` → `p-4`, section headers now `text-[11px] font-semibold uppercase tracking-wider`, dividers between sections, artifact pills larger
- Failure: full step list shown above error box, error header promoted to `text-sm font-semibold`
- Progress label: `text-[10px]` → `text-[11px]`

- [ ] **Step 2: Build and verify**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`

- [ ] **Step 3: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/src/components/a2ui/components/execution-surface.tsx && git commit -m "feat: improve execution surface typography, results hierarchy, failure context, and spinner"
```

---

### Task 3: Insight Surface — Reorder + Source + Action Hierarchy + Dismiss

**Files:**
- Modify: `frontend/src/components/a2ui/components/insight-surface.tsx`

- [ ] **Step 1: Rewrite the InsightSurface JSX**

Read the file. Replace the ENTIRE return JSX (the `<div className="space-y-3">` through closing `</div>`) with this reordered version:

```typescript
  return (
    <div className="space-y-3">
      {/* 1. Signal summary — headline first */}
      <p className="text-sm text-t-primary font-semibold">
        {insightData.signal_summary}
      </p>

      {/* 2. Source + relevance — compact metadata line */}
      <div className="flex items-center gap-1.5 text-xs text-t-muted">
        <span>{icon}</span>
        <span>{insightData.signal_source}</span>
        {insightData.relevance_score >= 0.7 && (
          <>
            <span>&middot;</span>
            <span className="text-j-warning font-medium">High relevance</span>
          </>
        )}
      </div>

      {/* 3. Relevance reasoning */}
      {insightData.relevance_reasoning && (
        <p className="text-xs text-t-tertiary">
          {insightData.relevance_reasoning}
        </p>
      )}

      {/* 4. Related goals */}
      {insightData.related_goals.length > 0 && (
        <div>
          <p className="text-[11px] text-t-muted font-medium uppercase tracking-wider mb-1.5">Related goals</p>
          <div className="flex flex-wrap gap-1">
            {insightData.related_goals.map((goal, i) => (
              <span
                key={i}
                className="text-[10px] px-1.5 py-0.5 rounded-full bg-j-info-soft text-j-info"
              >
                {goal}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 5. Suggested actions + dismiss */}
      {(insightData.suggested_actions.length > 0 || insightData.dismiss_available) && (
        <div className="flex flex-wrap items-center gap-2 pt-1">
          {insightData.suggested_actions.map((action, i) => (
            <button
              key={i}
              type="button"
              onClick={() => handleAction(i)}
              disabled={acting !== null}
              className={`text-xs px-3 py-1.5 rounded-[var(--radius-md)] transition-colors disabled:opacity-50 ${
                i === 0
                  ? "bg-j-primary text-j-primary-fg font-medium hover:bg-j-primary-hover"
                  : "bg-surface-2 text-t-secondary hover:bg-surface-3"
              }`}
            >
              {acting === i ? "Starting..." : action.description}
            </button>
          ))}
          {insightData.dismiss_available && (
            <button
              type="button"
              onClick={handleDismiss}
              disabled={dismissing}
              className="text-xs text-t-muted hover:text-t-secondary transition-colors disabled:opacity-50 ml-auto"
            >
              {dismissing ? "Dismissing..." : "Dismiss"}
            </button>
          )}
        </div>
      )}
    </div>
  );
```

Key changes:
- Signal summary moved to FIRST position, promoted to `font-semibold`
- Source badge simplified to compact text line below summary (no uppercase badge)
- Relevance threshold lowered from 0.8 to 0.7
- Related goals now have section label
- First action button is primary (`bg-j-primary`), rest are secondary (`bg-surface-2`)
- Dismiss moved inline with actions row (`ml-auto`), no separate border-t section

- [ ] **Step 2: Build and verify**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`

- [ ] **Step 3: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/src/components/a2ui/components/insight-surface.tsx && git commit -m "feat: reorder insight surface for value-first flow, add action hierarchy and inline dismiss"
```

---

### Task 4: Inline Approval Card — Step Promotion + Risk Color + Graduation + Buttons

**Files:**
- Modify: `frontend/src/components/a2ui/components/inline-approval.tsx`

- [ ] **Step 1: Rewrite the InlineApprovalCard JSX**

Read the file. Replace the ENTIRE return JSX with this updated version:

```typescript
  return (
    <div className="rounded-[var(--radius-lg)] border border-j-warning/30 bg-j-warning-soft p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="text-j-warning">&#9888;</span>
        <span className="text-sm font-medium text-t-primary">Approval Required</span>
      </div>

      {/* Step description — promoted to primary */}
      <p className="text-sm font-semibold text-t-primary">{approval.step_description}</p>

      {/* Risk reasoning — with warning accent border */}
      <div className="rounded-[var(--radius-md)] bg-surface-1 border-l-[3px] border-l-j-warning p-3 space-y-1.5">
        <div className="flex items-center gap-2">
          <p className="text-xs font-medium text-t-secondary">Risk Assessment</p>
          <span className="text-[10px] px-1.5 py-0.5 rounded-[var(--radius-sm)] bg-j-warning-soft text-j-warning font-medium uppercase">
            review
          </span>
        </div>
        <p className="text-xs text-t-tertiary">{approval.risk_reasoning}</p>
      </div>

      {/* Trust context — structured display */}
      <div className="text-xs">
        <span className="font-medium text-t-secondary">Trust: </span>
        <span className="text-t-tertiary">{approval.trust_context}</span>
      </div>

      {/* Graduation hint — callout box, moved above buttons */}
      {approval.graduation_hint && (
        <div className="bg-j-info-soft rounded-[var(--radius-md)] px-3 py-2 flex items-start gap-2">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" className="text-j-info shrink-0 mt-0.5">
            <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.3" />
            <path d="M8 7v4M8 5.5v0" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
          </svg>
          <p className="text-xs text-j-info">{approval.graduation_hint}</p>
        </div>
      )}

      {/* Action buttons — equal weight */}
      <div className="flex items-center gap-2.5 pt-1">
        <button
          type="button"
          onClick={handleApprove}
          className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-j-success text-white hover:bg-j-success/90 transition-colors cursor-pointer"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={handleEdit}
          className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-surface-2 text-t-secondary border border-b-secondary hover:bg-surface-3 transition-colors cursor-pointer"
        >
          Edit
        </button>
        <button
          type="button"
          onClick={handleReject}
          className="px-4 py-2 text-xs font-medium rounded-[var(--radius-md)] bg-j-error-soft text-j-error border border-j-error/20 hover:bg-j-error/15 transition-colors cursor-pointer"
        >
          Reject
        </button>
      </div>
    </div>
  );
```

Key changes:
- Step description promoted to `font-semibold text-t-primary`
- Risk box: added `border-l-[3px] border-l-j-warning` accent, added "review" badge
- Trust context: `font-medium` on label for clearer structure
- Graduation hint: moved ABOVE buttons, wrapped in `bg-j-info-soft` callout with info icon
- Buttons: Reject elevated to `bg-j-error-soft border border-j-error/20` (from text-only), all buttons now `px-4 py-2` (from `px-3 py-1.5`), gap `2.5` (from `2`)

- [ ] **Step 2: Build and verify**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -10`
Expected: `✓ Compiled successfully`

- [ ] **Step 3: Commit**

```bash
cd /Users/sivasankarreddybogala/work/jarvis && git add frontend/src/components/a2ui/components/inline-approval.tsx && git commit -m "feat: improve approval card with step promotion, risk accent, graduation callout, and equal-weight buttons"
```

---

### Task 5: Final Verification

- [ ] **Step 1: Build + lint**

Run: `cd /Users/sivasankarreddybogala/work/jarvis/frontend && npx next build 2>&1 | tail -15 && npm run lint 2>&1 | tail -10`
Expected: Both pass cleanly.

- [ ] **Step 2: Commit any fixes**

If any issues found:
```bash
git add -A frontend/src/ && git commit -m "fix: resolve Phase 3 verification issues"
```
