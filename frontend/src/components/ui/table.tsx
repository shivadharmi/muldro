import type { ReactNode } from "react";

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">{children}</table>
    </div>
  );
}

export function TableHeader({ children }: { children: ReactNode }) {
  return (
    <thead>
      <tr className="border-b border-neutral-800 text-left text-xs text-neutral-500 uppercase tracking-wider">
        {children}
      </tr>
    </thead>
  );
}

export function TableBody({ children }: { children: ReactNode }) {
  return <tbody className="divide-y divide-neutral-800/50">{children}</tbody>;
}

export function Th({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <th className={`px-4 py-2 font-medium ${className}`}>{children}</th>;
}

export function Td({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <td className={`px-4 py-2 text-neutral-300 ${className}`}>{children}</td>;
}
