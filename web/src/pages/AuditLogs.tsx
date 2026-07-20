import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { api } from "../lib/api";
import { PageHeader, Card, EmptyState, LoadingBlock } from "../components/ui";

interface AuditRow {
  id: number; summary: string; action: string; username: string | null; createdAt: string;
}

export default function AuditLogs() {
  const { data, isLoading } = useQuery<AuditRow[]>({
    queryKey: ["audit-logs"],
    queryFn: async () => (await api.get("/api/logs/audit")).data,
  });

  if (isLoading) return <LoadingBlock />;

  return (
    <div>
      <PageHeader title="Audit Logs" subtitle="A plain-English record of who did what, and when." />
      <Card>
        {!data || data.length === 0 ? (
          <EmptyState title="No activity recorded yet" icon={History} />
        ) : (
          <ul className="divide-y divide-ink-100">
            {data.map((row) => (
              <li key={row.id} className="flex items-start gap-3 px-5 py-3.5">
                <div className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-400" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm text-ink-800">{row.summary}</p>
                  <p className="mt-0.5 text-xs text-ink-400">{new Date(row.createdAt).toLocaleString()}</p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
