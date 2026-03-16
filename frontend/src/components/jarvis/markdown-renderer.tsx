"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Components } from "react-markdown";

const components: Components = {
  h1: ({ children }) => (
    <h1 className="text-xl font-bold mt-4 mb-2 text-neutral-100">{children}</h1>
  ),
  h2: ({ children }) => (
    <h2 className="text-lg font-semibold mt-3 mb-1.5 text-neutral-100">{children}</h2>
  ),
  h3: ({ children }) => (
    <h3 className="text-base font-semibold mt-2 mb-1 text-neutral-100">{children}</h3>
  ),
  h4: ({ children }) => (
    <h4 className="text-sm font-semibold mt-2 mb-1 text-neutral-200">{children}</h4>
  ),
  p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-blue-400 hover:text-blue-300 underline underline-offset-2"
    >
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="list-disc list-inside mb-2 space-y-0.5">{children}</ul>,
  ol: ({ children }) => (
    <ol className="list-decimal list-inside mb-2 space-y-0.5">{children}</ol>
  ),
  li: ({ children }) => <li className="text-neutral-200">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-neutral-600 pl-3 my-2 text-neutral-400 italic">
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
            <div className="text-[10px] uppercase tracking-wider text-neutral-500 bg-neutral-950 rounded-t px-3 py-1 border border-b-0 border-neutral-800">
              {lang}
            </div>
          )}
          <pre
            className={`bg-neutral-950 text-neutral-300 text-xs px-3 py-2 overflow-x-auto border border-neutral-800 ${lang ? "rounded-b" : "rounded"}`}
          >
            <code>{children}</code>
          </pre>
        </div>
      );
    }
    return (
      <code className="bg-neutral-700/50 text-neutral-200 px-1.5 py-0.5 rounded text-[0.85em] font-mono">
        {children}
      </code>
    );
  },
  pre: ({ children }) => <>{children}</>,
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="min-w-full text-sm border border-neutral-700 rounded">
        {children}
      </table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-neutral-800 text-neutral-300">{children}</thead>
  ),
  tbody: ({ children }) => <tbody className="divide-y divide-neutral-800">{children}</tbody>,
  tr: ({ children }) => <tr className="hover:bg-neutral-800/30">{children}</tr>,
  th: ({ children }) => (
    <th className="px-3 py-1.5 text-left text-xs font-medium uppercase tracking-wider text-neutral-400">
      {children}
    </th>
  ),
  td: ({ children }) => <td className="px-3 py-1.5 text-neutral-300">{children}</td>,
  hr: () => <hr className="my-3 border-neutral-700" />,
  strong: ({ children }) => (
    <strong className="font-semibold text-neutral-100">{children}</strong>
  ),
  em: ({ children }) => <em className="italic text-neutral-300">{children}</em>,
  del: ({ children }) => <del className="line-through text-neutral-500">{children}</del>,
  input: ({ checked, disabled }) => (
    <input
      type="checkbox"
      checked={checked}
      disabled={disabled}
      readOnly
      className="mr-1.5 accent-blue-500"
    />
  ),
};

export function MarkdownRenderer({ content }: { content: string }) {
  return (
    <div className="prose-jarvis">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
