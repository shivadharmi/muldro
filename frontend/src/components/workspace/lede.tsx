"use client";

import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";

/**
 * Slot 4 of the card: paragraph one of the model's body.
 *
 * Inline markdown ONLY. Block constructs destroy a 320px grid cell whatever
 * their length, and links are refused outright: a link inside a model-authored
 * body reopens injection through a different door. The model names a source;
 * the frame links it.
 *
 * `remarkGfm` is deliberately NOT applied - it autolinks bare URLs.
 */
const inlineOnly: Components = {
  p: ({ children }) => <span>{children}</span>,
  h1: ({ children }) => <span>{children}</span>,
  h2: ({ children }) => <span>{children}</span>,
  h3: ({ children }) => <span>{children}</span>,
  h4: ({ children }) => <span>{children}</span>,
  h5: ({ children }) => <span>{children}</span>,
  h6: ({ children }) => <span>{children}</span>,
  ul: ({ children }) => <span>{children}</span>,
  ol: ({ children }) => <span>{children}</span>,
  li: ({ children }) => <span>{children} </span>,
  blockquote: ({ children }) => <span>{children}</span>,
  pre: ({ children }) => <>{children}</>,
  hr: () => <span> </span>,
  img: () => null,
  // A link renders as its own text with no anchor.
  a: ({ children }) => <span>{children}</span>,
  strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  code: ({ children }) => (
    <code className="bg-surface-3 px-1 py-0.5 rounded-[var(--radius-sm)] text-[0.9em] font-mono">
      {children}
    </code>
  ),
};

export function Lede({ text }: { text: string }) {
  if (!text) return null;
  return (
    <p className="text-xs text-t-tertiary line-clamp-3 leading-relaxed">
      <ReactMarkdown components={inlineOnly}>{text}</ReactMarkdown>
    </p>
  );
}
