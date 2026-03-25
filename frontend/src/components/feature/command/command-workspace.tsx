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

      {/* Chat Panel — expands to fill when no surfaces */}
      <div className="flex-1 flex flex-col min-w-0">
        {commandPanel}
      </div>

      {/* Surface Panel — slides in from right when surfaces exist */}
      {surfaces && (
        <div
          className="w-[380px] shrink-0 border-l border-b-primary bg-surface-0
                     overflow-y-auto transition-all duration-200 ease-in-out"
        >
          {surfaces}
        </div>
      )}
    </div>
  );
}
