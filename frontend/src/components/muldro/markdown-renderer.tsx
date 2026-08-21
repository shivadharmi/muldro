"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

const components: Components = {
  h1: ({ children }) => (
    <h1 className="text-xl font-bold mt-4 mb-2 text-t-primary">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-lg font-semibold mt-3 mb-1.5 text-t-primary">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-base font-semibold mt-2 mb-1 text-t-primary">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="text-sm font-semibold mt-2 mb-1 text-t-primary">{children}</h4>
  ),
  p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-j-primary hover:text-j-primary underline underline-offset-2"
    >
      {children}
    </a>
  ),
  // No markdown renderer fetches a remote image, block prose included.
  //
  // This is sharper than a link because it needs no click: a tracking pixel
  // laundered into rendered markdown fires a remote fetch the moment the
  // component mounts, leaking the founder's IP and returning a read receipt
  // confirming the address is live and actively monitored. For a spam or
  // phishing campaign that receipt is frequently the actual objective.
  //
  // It could not stay half-closed at the inline renderer: insight.py's
  // `signal_summary` renders through `InlineMarkdown` on the card AND through
  // `markdown()` -> MarkdownRenderer in the detail tab, so the same string
  // would drop the pixel in one place and fetch it in the other.
  //
  // This forecloses images in chat prose, but forecloses nothing that exists:
  // no backend path emits `![...]` and the frontend renders no <img> anywhere.
  // When images ARE wanted (charts, diagrams, screenshots) the right shape is
  // a same-origin or `data:` URI allowlist, built deliberately — not inherited
  // by accident from a markdown default.
  img: () => null,
  ul: ({ children }) => <ul className="list-disc list-inside mb-2 space-y-0.5">{children}</ul>,
  ol: ({ children }) => (
    <ol className="list-decimal list-inside mb-2 space-y-0.5">{children}</ol>
  ),
  li: ({ children }) => <li className="text-t-primary">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-b-primary pl-3 my-2 text-t-secondary italic">
      {children}
    </blockquote>
  ),
  code: ({ className, children }) => {
    const isBlock = className?.includes("language-");
    if (isBlock) {
      const lang = className?.replace("language-", "") || "";
      return (
        <div className="my-2">
          {lang && (
            <div className="text-[10px] uppercase tracking-wider text-t-tertiary bg-surface-0 rounded-t-[var(--radius-sm)] px-3 py-1 border border-b-0 border-b-primary">
              {lang}
            </div>
          )}
          <pre
            className={`bg-surface-0 text-t-primary text-xs px-3 py-2 overflow-x-auto border border-b-primary ${lang ? "rounded-b-[var(--radius-sm)]" : "rounded-[var(--radius-sm)]"}`}
          >
            <code>{children}</code>
          </pre>
        </div>
      );
    }
    return (
      <code className="bg-surface-3 text-t-primary px-1.5 py-0.5 rounded-[var(--radius-sm)] text-[0.85em] font-mono">
        {children}
      </code>
    );
  },
  pre: ({ children }) => <>{children}</>,
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="min-w-full text-sm border border-b-primary rounded-[var(--radius-sm)]">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-surface-2 text-t-primary">{children}</thead>
  ),
  tbody: ({ children }) => <tbody className="divide-y divide-b-primary">{children}</tbody>,
  tr: ({ children }) => <tr className="hover:bg-surface-2/30">{children}</tr>,
  th: ({ children }) => (
    <th className="px-3 py-1.5 text-left text-xs font-medium uppercase tracking-wider text-t-secondary">
      {children}
    </th>
  ),
  td: ({ children }) => <td className="px-3 py-1.5 text-t-primary">{children}</td>,
  hr: () => <hr className="my-3 border-b-primary" />,
  strong: ({ children }) => (
    <strong className="font-semibold text-t-primary">{children}</strong>
  ),
  em: ({ children }) => <em className="italic text-t-primary">{children}</em>,
  del: ({ children }) => <del className="line-through text-t-tertiary">{children}</del>,
  input: ({ checked, disabled }) => (
    <input
      type="checkbox"
      checked={checked}
      disabled={disabled}
      readOnly
      className="mr-1.5 accent-[var(--muldro-primary)]"
    />
  ),
};

export function MarkdownRenderer({ content }: { content: string }) {
  return (
    <div className="prose-muldro">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

// InlineMarkdown is used inside heading/caption contexts (alert titles, A2UI
// captions, insight summaries). Block-level tags (headings, lists, blockquotes)
// would produce invalid HTML when nested inside inline parents, so override
// them to <span>.
//
// `a` is overridden to a plain <span> as well: every remaining call site is a
// short string in a heading or caption position, several of them derived from
// external text, and a live `target="_blank"` link there puts an attacker's URL
// in muldro's voice with no sender attributed. `remarkGfm` makes this wider than
// `[text](url)` — it autolinks bare `www.…` and bare email addresses too. Block
// prose (`MarkdownRenderer`) keeps its links; inline strings do not get one.
// `img` is NOT overridden here: it is refused in the base map above, and
// inline inherits that by spreading it. Two lines doing one job is how a pair
// drifts.
const inlineComponents: Components = {
  ...components,
  a: ({ children }) => <span>{children}</span>,
  p: ({ children }) => <span>{children}</span>,
  h1: ({ children }) => <span className="font-semibold">{children}</span>,
  h2: ({ children }) => <span className="font-semibold">{children}</span>,
  h3: ({ children }) => <span className="font-semibold">{children}</span>,
  h4: ({ children }) => <span className="font-semibold">{children}</span>,
  ul: ({ children }) => <span>{children}</span>,
  ol: ({ children }) => <span>{children}</span>,
  li: ({ children }) => <span>{children} </span>,
  blockquote: ({ children }) => <span className="italic">{children}</span>,
  hr: () => <span> · </span>,
  pre: ({ children }) => <>{children}</>,
};

/** Compact markdown for short text (summaries, descriptions). No block-level spacing. */
export function InlineMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={inlineComponents}>
      {content}
    </ReactMarkdown>
  );
}
