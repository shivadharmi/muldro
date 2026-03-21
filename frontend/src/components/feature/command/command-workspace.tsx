"use client";

interface Props {
  sessionRail: React.ReactNode;
  commandPanel: React.ReactNode;
  surfaces?: React.ReactNode;
}

export function CommandWorkspace({ sessionRail, commandPanel, surfaces }: Props) {
  return (
    <div className="flex h-full">
      {/* Session Rail */}
      <div className="w-64 shrink-0 border-r border-b-primary overflow-y-auto hidden lg:block">
        {sessionRail}
      </div>

      {/* Central Command + Surfaces */}
      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex-1 overflow-y-auto">{commandPanel}</div>
        {surfaces && (
          <div className="border-t border-b-primary p-3">{surfaces}</div>
        )}
      </div>
    </div>
  );
}
