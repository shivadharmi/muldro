export function EmptyState({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div className="text-center py-12">
      <p className="text-neutral-500 text-sm font-medium">{title}</p>
      {description && <p className="text-neutral-600 text-xs mt-1">{description}</p>}
    </div>
  );
}
