"use client";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  // Never render a raw message/stack. For ApiError use the safe envelope
  // message; otherwise a generic line. Surface a reference id (the API
  // correlation id, or Next's `digest`) so users can report the failure.
  const safeMessage =
    error instanceof ApiError ? error.displayMessage : "An unexpected error occurred.";
  const reference =
    error instanceof ApiError ? error.correlationId : error.digest;

  return (
    <div className="flex items-center justify-center min-h-[50vh] p-6">
      <div className="text-center max-w-md">
        <div className="text-4xl mb-4 text-t-muted">!</div>
        <h2 className="text-lg font-semibold text-t-primary mb-2">
          Something went wrong
        </h2>
        <p className="text-sm text-t-tertiary mb-4">{safeMessage}</p>
        {reference && !(error instanceof ApiError) && (
          <p className="text-xs text-t-muted mb-4">reference: {reference}</p>
        )}
        <Button onClick={reset}>Try again</Button>
      </div>
    </div>
  );
}
