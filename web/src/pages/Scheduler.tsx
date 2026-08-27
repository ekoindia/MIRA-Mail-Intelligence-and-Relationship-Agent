import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileEdit, Clock, Inbox, RefreshCw } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { PageHeader, Card, CardHeader, Button, Badge, EmptyState, Toggle } from "../components/ui";

interface ReportItem {
  id: number; reportName: string; frequency: string | null; orgLevels: string[]; templateName: string | null;
}

interface AutomationStatus {
  autosendEnabled: boolean; fetchTime: string; sendTime: string;
  skipWeekdays: number[]; skipWeekdayNames: string[]; skippedToday: boolean;
}

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

interface IncomingSyncStatus { syncEnabled: boolean }
interface IncomingSyncResult {
  ingest: { scanned: number; new: number; attachments: number; drafts: number; needs_review: number; errors: number; error?: string };
  replies_updated: number;
  recipient_kind_backfilled: number;
}

function IncomingSyncCard() {
  const queryClient = useQueryClient();
  const [result, setResult] = useState<IncomingSyncResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { data: status } = useQuery<IncomingSyncStatus>({
    queryKey: ["incoming-sync-status"],
    queryFn: async () => (await api.get("/api/incoming/sync-status")).data,
  });

  const toggleSync = useMutation({
    mutationFn: async (enabled: boolean) => (await api.patch("/api/incoming/sync-toggle", { enabled })).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["incoming-sync-status"] }),
  });

  const sync = useMutation({
    mutationFn: async () => (await api.post("/api/incoming/sync")).data as IncomingSyncResult,
    onSuccess: (data) => {
      setResult(data);
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["incoming-summary"] });
      queryClient.invalidateQueries({ queryKey: ["incoming-patterns"] });
      queryClient.invalidateQueries({ queryKey: ["incoming-recent"] });
      queryClient.invalidateQueries({ queryKey: ["incoming-recipient-kind"] });
      queryClient.invalidateQueries({ queryKey: ["incoming-reply-match-summary"] });
    },
    onError: (err) => {
      setResult(null);
      setError(apiErrorMessage(err, "Sync failed."));
    },
  });

  return (
    <Card className="mb-5">
      <div className="flex items-center justify-between gap-3 px-5 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
            <Inbox className="h-4.5 w-4.5" strokeWidth={2.25} />
          </div>
          <div className="text-sm text-ink-700">
            {status ? (
              <>
                Incoming mail sync: polls the connected inbox every 5 minutes, detect-and-score only —{" "}
                <Badge tone={status.syncEnabled ? "green" : "slate"}>
                  {status.syncEnabled ? "ON" : "OFF"}
                </Badge>
              </>
            ) : (
              "Loading incoming sync status…"
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <Toggle
            checked={!!status?.syncEnabled}
            onChange={() => toggleSync.mutate(!status?.syncEnabled)}
            disabled={!status || toggleSync.isPending}
          />
          <Button size="sm" onClick={() => sync.mutate()} disabled={sync.isPending}>
            <RefreshCw className={`h-3.5 w-3.5 ${sync.isPending ? "animate-spin" : ""}`} strokeWidth={2.25} />
            {sync.isPending ? "Syncing…" : "Sync now"}
          </Button>
        </div>
      </div>

      {(error || result) && (
        <div className="border-t border-ink-100 px-5 py-3">
          {error && <div className="rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}
          {result && (
            <div className="rounded-md bg-info-soft border border-info-line px-3 py-2 text-xs text-info-fg">
              Scanned {result.ingest.scanned}, {result.ingest.new} new, {result.replies_updated} reply status(es) updated,{" "}
              {result.recipient_kind_backfilled} recipient-kind backfilled.
              {result.ingest.errors > 0 && ` ${result.ingest.errors} error(s).`}
            </div>
          )}
        </div>
      )}
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
                  {status.skipWeekdayNames.length > 0 && (
                    <div className="mt-1 text-xs text-ink-500">
                      No-send days: <span className="font-medium">{status.skipWeekdayNames.join(", ")}</span>
                      {status.skippedToday && (
                        <span className="ml-2 rounded-full bg-warn-soft px-2 py-0.5 font-medium text-warn-fg">
                          Today is a no-send day — nothing will go out
                        </span>
                      )}
                    </div>
                  )}
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

      <IncomingSyncCard />

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
