"use client";

import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchConversations,
  updateConversation,
  deleteConversation,
} from "@/lib/api";
import type { ConversationSummary } from "@/lib/api";
import { PageHeader } from "@/components/layout/page-header";
import { Tabs } from "@/components/ui/tabs";
import { Card, CardBody } from "@/components/ui/card";
import { Badge, statusVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TimeAgo } from "@/components/ui/time-ago";
import { useToast } from "@/components/ui/toast";
import Link from "next/link";

const STATUS_TABS = [
  { key: "active", label: "Active" },
  { key: "archived", label: "Archived" },
  { key: "all", label: "All" },
];

export default function ConversationsPage() {
  const queryClient = useQueryClient();
  const { addToast } = useToast();
  const [statusFilter, setStatusFilter] = useState("active");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const statusParam = statusFilter === "all" ? undefined : statusFilter;
  const { data: conversations = [], isLoading } = useQuery({
    queryKey: ["conversations", statusFilter],
    queryFn: () => fetchConversations(statusParam),
    refetchInterval: 30_000,
  });

  const archiveMut = useMutation({
    mutationFn: (id: string) => updateConversation(id, { status: "archived" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      addToast("Conversation archived", "success");
    },
    onError: (err) => addToast(`Failed to archive: ${err.message}`, "error"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      addToast("Conversation deleted", "success");
    },
    onError: (err) => addToast(`Failed to delete: ${err.message}`, "error"),
  });

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function bulkArchive() {
    selected.forEach((id) => archiveMut.mutate(id));
    setSelected(new Set());
  }

  function bulkDelete() {
    selected.forEach((id) => deleteMut.mutate(id));
    setSelected(new Set());
  }

  return (
    <div className="p-6 space-y-6">
      <PageHeader
        title="Conversations"
        subtitle="Manage chat conversations"
      />

      <div className="flex items-center justify-between">
        <Tabs tabs={STATUS_TABS} active={statusFilter} onChange={setStatusFilter} />
        {selected.size > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-neutral-500">{selected.size} selected</span>
            <Button size="sm" variant="secondary" onClick={bulkArchive}>
              Archive
            </Button>
            <Button size="sm" variant="danger" onClick={bulkDelete}>
              Delete
            </Button>
          </div>
        )}
      </div>

      {isLoading && (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}>
              <CardBody>
                <div className="animate-pulse space-y-2">
                  <div className="h-4 w-48 bg-neutral-800 rounded" />
                  <div className="h-3 w-64 bg-neutral-800 rounded" />
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      {!isLoading && conversations.length === 0 && (
        <div className="text-center py-12">
          <p className="text-neutral-500 text-sm font-medium">No conversations</p>
          <p className="text-neutral-600 text-xs mt-1">
            Start a chat to create your first conversation.
          </p>
        </div>
      )}

      <div className="space-y-2">
        {conversations.map((conv: ConversationSummary) => (
          <Card key={conv.conversation_id} className={selected.has(conv.conversation_id) ? "border-blue-700" : ""}>
            <CardBody>
              <div className="flex items-start gap-3">
                <input
                  type="checkbox"
                  checked={selected.has(conv.conversation_id)}
                  onChange={() => toggleSelect(conv.conversation_id)}
                  className="mt-1 accent-blue-600"
                />
                <Link
                  href={`/chat?conversation=${conv.conversation_id}`}
                  className="flex-1 min-w-0"
                >
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-medium text-white truncate">
                      {conv.title || "Untitled"}
                    </h3>
                    <div className="flex items-center gap-2 ml-2 shrink-0">
                      <Badge variant={statusVariant(conv.status)}>{conv.status}</Badge>
                      <Badge variant="default">{conv.surface}</Badge>
                    </div>
                  </div>
                  {conv.preview && (
                    <p className="text-xs text-neutral-400 mt-0.5 truncate">{conv.preview}</p>
                  )}
                  <div className="flex items-center gap-4 mt-1 text-[10px] text-neutral-600">
                    <span>{conv.message_count} messages</span>
                    {conv.total_cost_usd > 0 && (
                      <span>${conv.total_cost_usd.toFixed(4)}</span>
                    )}
                    {conv.last_active_at && <TimeAgo date={conv.last_active_at} />}
                  </div>
                </Link>
                <div className="flex items-center gap-1 shrink-0">
                  {conv.status === "active" && (
                    <Button
                      size="sm"
                      variant="secondary"
                      onClick={(e: React.MouseEvent) => {
                        e.preventDefault();
                        archiveMut.mutate(conv.conversation_id);
                      }}
                    >
                      Archive
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={(e: React.MouseEvent) => {
                      e.preventDefault();
                      deleteMut.mutate(conv.conversation_id);
                    }}
                  >
                    Delete
                  </Button>
                </div>
              </div>
            </CardBody>
          </Card>
        ))}
      </div>
    </div>
  );
}
