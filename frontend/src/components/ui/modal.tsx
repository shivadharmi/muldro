"use client";

import type { ReactNode } from "react";

export function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative bg-neutral-900 border border-neutral-800 rounded-lg shadow-xl w-full max-w-lg mx-4">
        <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800">
          <h3 className="text-sm font-medium">{title}</h3>
          <button onClick={onClose} className="text-neutral-500 hover:text-neutral-300">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}
