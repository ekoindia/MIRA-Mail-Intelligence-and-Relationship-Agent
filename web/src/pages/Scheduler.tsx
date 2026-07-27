import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileEdit, Clock } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { PageHeader, Card, CardHeader, Button, Badge, EmptyState, Toggle } from "../components/ui";

interface ReportItem {
  id: number; reportName: string; frequency: string | null; orgLevels: string[]; templateName: string | null;
}

interface AutomationStatus { autosendEnabled: boolean; fetchTime: string; sendTime: string }

interface SendResult {
  // Daily results are per-report; Weekly/Monthly results are per recipient
  // level (one combined email covering every automated report mapped to
  // that level) — see api/routers/reports.py send_by_frequency.
  reportId?: number; reportName?: string;
  level?: string; reportNames?: string[];
  skipped: boolean; reason?: string;
  jobId?: number; recipientCount?: number; sent?: number; failed?: number; deliveryMode?: string;
}

function resultLabel(r: SendResult): string {
  if (r.level) return `${r.level} (${(r.reportNames ?? []).join(", ")})`;
  return r.reportName ?? "";
}

const FREQUENCIES = ["Daily", "Weekly", "Monthly"] as const;
const FREQUENCY_TONE: Record<string, "blue" | "amber" | "green"> = { Daily: "blue", Weekly: "amber", Monthly: "green" };

function FrequencyGroup({ frequency, reports }: { frequency: string; reports: ReportItem[] }) {
  const [results, setResults] = useState<SendResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const trigger = useMutation({
    mutationFn: async () =>
      (await api.post("/api/reports/send-by-frequency", { frequency, mode: "draft" })).data as { results: SendResult[] },
    onSuccess: (data) => {
      setResults(data.results);
      setError(null);
    },
    onError: (err) => {
      setResults(null);
      setError(apiErrorMessage(err, "Couldn't trigger this frequency group."));
    },
  });

  return (
    <Card>
      <CardHeader
        title={frequency}
        subtitle={`${reports.length} report(s)`}
        action={
          <Button size="sm" onClick={() => trigger.mutate()} disabled={trigger.isPending}>
            <FileEdit className="h-3.5 w-3.5" strokeWidth={2.25} />
            {trigger.isPending ? "Working…" : `Draft ${frequency}`}
          </Button>
        }
      />
      <div className="px-5 py-4">
        <div className="flex flex-wrap gap-1.5">
          {reports.map((r) => (
            <Badge key={r.id} tone={FREQUENCY_TONE[frequency] ?? "slate"}>{r.reportName}</Badge>
          ))}
        </div>

        {error && <div className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}

        {results && (
          <div className="mt-3 space-y-1.5">
            {results.map((r, i) => (
              <div
                key={r.reportId ?? r.level ?? i}
                className={`rounded-md px-3 py-2 text-xs ${r.skipped ? "bg-ink-50 text-ink-500" : "bg-emerald-50 text-emerald-800"}`}
              >
                <span className="font-medium">{resultLabel(r)}</span>
                {r.skipped
                  ? <> — skipped: {r.reason}</>
                  : <> — {r.deliveryMode === "draft" ? "drafted" : "sent"} to {r.recipientCount} recipient(s)
                      {r.failed ? `, ${r.failed} failed` : ""}.</>}
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}

export default function Scheduler() {
  const queryClient = useQueryClient();
  const { data: reports, isLoading } = useQuery<ReportItem[]>({
    queryKey: ["reports"],
    queryFn: async () => (await api.get("/api/reports")).data,
  });
  const { data: status } = useQuery<AutomationStatus>({
    queryKey: ["automation-status"],
    queryFn: async () => (await api.get("/api/automation/status")).data,
  });

  const toggleAutosend = useMutation({
    mutationFn: async (enabled: boolean) => (await api.patch("/api/automation/autosend", { enabled })).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["automation-status"] }),
  });

  if (isLoading) return null;

  const byFrequency = FREQUENCIES.map((f) => ({
    frequency: f as string,
    reports: (reports ?? []).filter((r) => r.frequency === f),
  })).filter((g) => g.reports.length > 0);

  return (
    <div>
      <PageHeader title="Scheduler" subtitle="Trigger drafting for a whole frequency group, right now, or leave it to the automatic daily cycle." />

      <Card className="mb-5">
        <div className="flex items-center justify-between gap-3 px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
              <Clock className="h-4.5 w-4.5" strokeWidth={2.25} />
            </div>
            <div className="text-sm text-ink-700">
              {status ? (
                <>
                  Automatic daily cycle: fetch at <span className="font-medium">{status.fetchTime}</span> (rechecks
                  hourly until the calling sheet updates), drafts once fresh at/after{" "}
                  <span className="font-medium">{status.sendTime}</span> —{" "}
                  <Badge tone={status.autosendEnabled ? "green" : "slate"}>
                    {status.autosendEnabled ? "ON" : "OFF"}
                  </Badge>
                </>
              ) : (
                "Loading automation status…"
              )}
            </div>
          </div>
          <Toggle
            checked={!!status?.autosendEnabled}
            onChange={() => toggleAutosend.mutate(!status?.autosendEnabled)}
            disabled={!status || toggleAutosend.isPending}
          />
        </div>
      </Card>

      <div className="space-y-5">
        {byFrequency.length === 0 ? (
          <EmptyState title="No reports configured yet" />
        ) : (
          byFrequency.map((g) => <FrequencyGroup key={g.frequency} frequency={g.frequency} reports={g.reports} />)
        )}
      </div>
    </div>
  );
}
