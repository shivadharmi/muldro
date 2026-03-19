"use client";

import { Button } from "@/components/ui/button";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="flex items-center justify-center min-h-[50vh] p-6">
      <div className="text-center max-w-md">
        <div className="text-4xl mb-4 text-t-muted">!</div>
        <h2 className="text-lg font-semibold text-t-primary mb-2">
          Something went wrong
        </h2>
        <p className="text-sm text-t-tertiary mb-4">
          {error.message || "An unexpected error occurred."}
        </p>
        <Button onClick={reset}>Try again</Button>
      </div>
    </div>
  );
}
