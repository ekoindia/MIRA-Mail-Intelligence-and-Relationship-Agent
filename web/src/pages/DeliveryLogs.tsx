import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { PageHeader, Card, EmptyState, Table, Th, Td, Badge, LoadingBlock } from "../components/ui";

interface LogRow {
  id: number; recipientName: string; recipientEmail: string; recipientType: string; report: string;
  status: string; channel: string | null; attempts: number; sentAt: string | null; error: string | null;
}

const STATUS_TONE: Record<string, "green" | "red" | "amber"> = { Sent: "green", Failed: "red", Retrying: "amber", Pending: "amber" };

export default function DeliveryLogs() {
  const { data, isLoading } = useQuery<LogRow[]>({
    queryKey: ["delivery-logs"],
    queryFn: async () => (await api.get("/api/logs/delivery")).data,
  });

  if (isLoading) return <LoadingBlock />;

  return (
    <div>
      <PageHeader title="Delivery Logs" subtitle="Every email sent, with status and delivery channel." />
      <Card>
        {!data || data.length === 0 ? (
          <EmptyState title="No emails sent yet" />
        ) : (
          <Table>
            <thead>
              <tr><Th>Recipient</Th><Th>Type</Th><Th>Report</Th><Th>Status</Th><Th>Channel</Th><Th>Sent At</Th></tr>
            </thead>
            <tbody>
              {data.map((r) => (
                <tr key={r.id}>
                  <Td>
                    <div className="font-medium text-ink-900">{r.recipientName}</div>
                    <div className="text-xs text-ink-400">{r.recipientEmail}</div>
                  </Td>
                  <Td><Badge tone="slate">{r.recipientType}</Badge></Td>
                  <Td>{r.report}</Td>
                  <Td>
                    <Badge tone={STATUS_TONE[r.status] ?? "slate"}>{r.status}</Badge>
                    {r.error && <div className="mt-1 max-w-xs truncate text-xs text-rose-500">{r.error}</div>}
                  </Td>
                  <Td className="capitalize">{r.channel ?? "—"}</Td>
                  <Td>{r.sentAt ? new Date(r.sentAt).toLocaleString() : "—"}</Td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </div>
  );
}
