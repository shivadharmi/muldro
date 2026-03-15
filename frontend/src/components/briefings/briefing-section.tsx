import { Card, CardBody, CardHeader } from "@/components/ui/card";

export function BriefingSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <span className="text-sm font-medium">{title}</span>
      </CardHeader>
      <CardBody>{children}</CardBody>
    </Card>
  );
}
