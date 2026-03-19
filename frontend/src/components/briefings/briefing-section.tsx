import { Card, CardBody, CardHeader } from "@/components/ui/card";

type SectionVariant = "priority" | "action" | "info";

const variantStyles: Record<SectionVariant, string> = {
  priority: "border-l-3 border-l-j-error",
  action: "border-l-3 border-l-j-primary",
  info: "",
};

export function BriefingSection({
  title,
  children,
  variant = "info",
}: {
  title: string;
  children: React.ReactNode;
  variant?: SectionVariant;
}) {
  return (
    <Card className={variantStyles[variant]}>
      <CardHeader>
        <span className="text-sm font-medium text-t-primary">{title}</span>
      </CardHeader>
      <CardBody>{children}</CardBody>
    </Card>
  );
}
