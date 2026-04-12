"use client";

import { useState } from "react";

interface Props {
  sessionRail: React.ReactNode;
  commandPanel: React.ReactNode;
  surfaces?: React.ReactNode;
}

export function CommandWorkspace({ sessionRail, commandPanel, surfaces }: Props) {
  const [mobileSessionOpen, setMobileSessionOpen] = useState(false);

  return (
    <div className="flex h-full">
      {/* Mobile session drawer */}
      {mobileSessionOpen && (
        <div
          className="fixed inset-0 bg-black/50 backdrop-blur-sm z-30 lg:hidden"
          onClick={() => setMobileSessionOpen(false)}
        />
      )}
      <div
        className={`
          fixed inset-y-0 left-0 z-40 w-72 transform transition-transform duration-200 ease-out lg:hidden
          ${mobileSessionOpen ? "translate-x-0" : "-translate-x-full"}
        `}
      >
        {sessionRail}
      </div>

      {/* Desktop session rail */}
      <div className="w-64 shrink-0 border-r border-b-secondary overflow-y-auto hidden lg:block">
        {sessionRail}
      </div>

      {/* Chat panel */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Mobile sessions toggle */}
        <div className="lg:hidden flex items-center px-3 py-1.5 border-b border-b-secondary">
          <button
            onClick={() => setMobileSessionOpen(true)}
            className="p-1.5 rounded-[var(--radius-md)] text-t-muted hover:text-t-primary hover:bg-surface-2 transition-colors cursor-pointer"
            aria-label="Open conversations"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
              <path d="M3 5h12M3 9h12M3 13h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            </svg>
          </button>
          <span className="text-xs text-t-muted ml-2">Conversations</span>
        </div>
        {commandPanel}
      </div>

      {/* Surfaces panel */}
      {surfaces && (
        <div className="w-[380px] shrink-0 border-l border-b-secondary bg-surface-0 overflow-y-auto transition-all duration-200 ease-in-out hidden md:block">
          {surfaces}
        </div>
      )}
    </div>
  );
}
