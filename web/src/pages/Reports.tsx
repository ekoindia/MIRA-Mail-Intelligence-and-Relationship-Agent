import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { PageHeader, Card, EmptyState, Table, Th, Td, Badge } from "../components/ui";

interface MappingReport { name: string; automated: boolean }
interface MappingRow {
  frequency: string; level: string; reports: MappingReport[];
  templateId: number | null; templateName: string | null;
}

const FREQUENCY_TONE: Record<string, "blue" | "amber" | "green"> = {
  Daily: "blue", Weekly: "amber", Monthly: "green",
};

function MappingTable({ rows }: { rows: MappingRow[] }) {
  return (
    <Card>
      {rows.length === 0 ? (
        <EmptyState title="No report mapping configured yet" />
      ) : (
        <Table>
          <thead>
            <tr><Th>Frequency</Th><Th>Level</Th><Th>Reports Sent In This Mail</Th><Th>Template</Th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.frequency}-${row.level}`}>
                <Td><Badge tone={FREQUENCY_TONE[row.frequency] ?? "slate"}>{row.frequency}</Badge></Td>
                <Td className="font-medium text-ink-900">{row.level}</Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    {row.reports.map((r) => (
                      <Badge key={r.name} tone={r.automated ? "green" : "slate"}>
                        {r.name}{!r.automated && " (paused)"}
                      </Badge>
                    ))}
                  </div>
                </Td>
                <Td>{row.templateName ?? <span className="text-ink-400">No template configured</span>}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

export default function Reports() {
  const { data: rows } = useQuery<MappingRow[]>({
    queryKey: ["report-mapping"],
    queryFn: async () => (await api.get("/api/reports/mapping")).data,
  });

  return (
    <div>
      <PageHeader
        title="Reports"
        subtitle="One combined email per recipient level per frequency — every automated report mapped to that level lands in a single mail. Drafting is triggered from Scheduler; the daily cycle on/off is in Settings."
      />

      <MappingTable rows={rows ?? []} />
    </div>
  );
}
