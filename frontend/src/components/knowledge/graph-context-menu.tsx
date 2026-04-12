"use client";

import { useEffect, useRef } from "react";

interface GraphContextMenuProps {
  x: number;
  y: number;
  entityId: string;
  entityName: string;
  onClose: () => void;
  onFocus: (entityId: string) => void;
  onExpand: (entityId: string, depth: number) => void;
  onHide: (entityId: string) => void;
  onViewMemories: (entityId: string) => void;
}

interface MenuItem {
  label: string;
  icon: string;
  action: () => void;
}

export function GraphContextMenu({
  x,
  y,
  entityId,
  entityName,
  onClose,
  onFocus,
  onExpand,
  onHide,
  onViewMemories,
}: GraphContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    }

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [onClose]);

  const items: MenuItem[] = [
    {
      label: "Focus here",
      icon: "\u2316", // crosshair
      action: () => {
        onFocus(entityId);
        onClose();
      },
    },
    {
      label: "Expand 2 hops",
      icon: "\u21C4", // arrows
      action: () => {
        onExpand(entityId, 2);
        onClose();
      },
    },
    {
      label: "Hide node",
      icon: "\u2717", // cross
      action: () => {
        onHide(entityId);
        onClose();
      },
    },
    {
      label: "View memories",
      icon: "\u2630", // menu
      action: () => {
        onViewMemories(entityId);
        onClose();
      },
    },
  ];

  const truncatedName =
    entityName.length > 24 ? entityName.slice(0, 24) + "\u2026" : entityName;

  return (
    <div
      ref={menuRef}
      className="fixed z-50 bg-surface-1 border border-b-secondary shadow-lg rounded-lg py-1 min-w-[180px]"
      style={{ left: x, top: y }}
    >
      {/* Header */}
      <div className="px-3 py-2 border-b border-b-secondary">
        <p className="text-xs font-medium text-t-primary truncate">
          {truncatedName}
        </p>
      </div>

      {/* Menu items */}
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          onClick={item.action}
          className="w-full text-left px-3 py-2 text-sm text-t-secondary hover:bg-surface-2 cursor-pointer transition-colors flex items-center gap-2"
        >
          <span className="text-t-muted w-4 text-center">{item.icon}</span>
          <span>{item.label}</span>
        </button>
      ))}
    </div>
  );
}
