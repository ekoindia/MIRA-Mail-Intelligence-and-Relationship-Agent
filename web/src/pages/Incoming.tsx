import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Inbox, Reply, Sparkles, ListChecks, ChevronDown,
} from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import {
  Card, CardHeader, Button, Badge, Toggle, EmptyState, LoadingBlock, Table, Th, Td,
} from "../components/ui";
import IncomingAckDashboard from "../components/IncomingAckDashboard";
import IncomingMailBreakdown from "../components/IncomingMailBreakdown";

interface AutomationSummary {
  total_incoming: number; total_replied: number; reply_rate: number;
  direct_total: number; cc_total: number; direct_replied: number; direct_reply_rate: number;
}
interface TriageSummary {
  total: number;
  by_tier: { tier: string; count: number; pct: number; intents: { intent: string; count: number }[] }[];
}
interface QueueTask {
  id: number; emailId: number; taskType: string;
  identifier: string | null; identifierKind: string | null;
  subject: string; sender: string; receivedAt: string | null;
  status: string; resolvedAt: string | null; resolvedBy: string | null;
}
interface TaskQueueSummary {
  totals: { open: number; done: number; dismissed: number };
  by_type: { taskType: string; open: number; done: number; dismissed: number }[];
}
interface LimitForwardStatus {
  enabled: boolean; since: string | null; forwardTo: string; forwardCc: string;
}

// Matches the Outgoing view's cadence so both halves feel equally live.
const LIVE_REFRESH_MS = 45_000;

function relativeTime(ts: number | undefined): string {
  if (!ts) return "just now";
  const secs = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (secs < 10) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  return mins < 60 ? `${mins}m ago` : `${Math.round(mins / 60)}h ago`;
}

/** Date AND time in the viewer's own zone. The API sends UTC with a 'Z',
 *  so Date parses it correctly instead of assuming local. */
function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true,
  });
}

interface SyncResult {
  ingest: { scanned: number; new: number; attachments: number; drafts: number; needs_review: number; errors: number; error?: string };
  replies_updated: number;
}

function CountTile({
  label, value, sub, icon: Icon, tone,
}: { label: string; value: string | number; sub: string; icon: React.ComponentType<{ className?: string; strokeWidth?: number }>; tone: "brand" | "sky" | "rose" | "amber" | "green" }) {
  const toneClasses: Record<string, string> = {
    brand: "bg-brand-50 text-brand-600", sky: "bg-sky-50 text-sky-600", rose: "bg-rose-50 text-rose-600",
    amber: "bg-amber-50 text-amber-600", green: "bg-emerald-50 text-emerald-600",
  };
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-500">{label}</span>
        <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${toneClasses[tone]}`}>
          <Icon className="h-4 w-4" strokeWidth={2.25} />
        </div>
      </div>
      <div className="mt-3 font-mono text-3xl font-semibold tracking-tight tabular-nums text-ink-900">{value}</div>
      <div className="mt-2 text-xs text-ink-500">{sub}</div>
    </Card>
  );
}

function PercentTile({
  label, percent, sub, icon: Icon,
}: { label: string; percent: number; sub: string; icon: React.ComponentType<{ className?: string; strokeWidth?: number }> }) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-500">{label}</span>
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          <Icon className="h-4 w-4" strokeWidth={2.25} />
        </div>
      </div>
      <div className="mt-3 font-mono text-3xl font-semibold tracking-tight tabular-nums text-ink-900">{percent}%</div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-ink-100">
        <div className="h-1.5 rounded-full bg-brand-500 transition-all" style={{ width: `${Math.min(100, percent)}%` }} />
      </div>
      <div className="mt-2 text-xs text-ink-500">{sub}</div>
    </Card>
  );
}

/** Owns every query/mutation behind the Incoming live-sync indicator, so the
 *  status dot + toggle + "Sync now" control can live in the page header
 *  (same slot as the Outgoing view's live indicator) while the section body
 *  below still reads from the same data. Call this once in the parent. */
export function useIncomingLive() {
  const queryClient = useQueryClient();
  const [syncError, setSyncError] = useState<string | null>(null);
  const [lastSyncResult, setLastSyncResult] = useState<SyncResult | null>(null);

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["incoming-summary"] });
    queryClient.invalidateQueries({ queryKey: ["incoming-triage-summary-sbi"] });
    queryClient.invalidateQueries({ queryKey: ["incoming-messages"] });
    queryClient.invalidateQueries({ queryKey: ["incoming-tasks"] });
    queryClient.invalidateQueries({ queryKey: ["incoming-tasks-summary"] });
  };

  const { data: summary, isLoading: summaryLoading, isFetching: summaryFetching } =
    useQuery<AutomationSummary>({
      queryKey: ["incoming-summary"],
      queryFn: async () => (await api.get("/api/incoming/automation-summary", { params: { sbi_only: true } })).data,
      refetchInterval: LIVE_REFRESH_MS,
    });
  const { data: syncStatus } = useQuery<{ syncEnabled: boolean }>({
    queryKey: ["incoming-sync-status"],
    queryFn: async () => (await api.get("/api/incoming/sync-status")).data,
  });
  // SBI-scoped, same query IncomingMailBreakdown uses ("incoming-triage-
  // summary-sbi") — shared cache entry, one real fetch backs both the
  // page-level "No Reply Needed" KPI and the breakdown widget below it.
  const { data: triage, isFetching: triageFetching, dataUpdatedAt: triageUpdatedAt } =
    useQuery<TriageSummary>({
      queryKey: ["incoming-triage-summary-sbi"],
      queryFn: async () => (await api.get("/api/incoming/triage-summary", { params: { sbi_only: true } })).data,
      refetchInterval: LIVE_REFRESH_MS,
    });

  const sync = useMutation({
    mutationFn: async () => (await api.post("/api/incoming/sync")).data as SyncResult,
    onSuccess: (data) => {
      setSyncError(null);
      setLastSyncResult(data);
      invalidateAll();
    },
    onError: (err) => setSyncError(apiErrorMessage(err, "Sync failed.")),
  });

  const toggleAutoSync = useMutation({
    mutationFn: async (enabled: boolean) => (await api.patch("/api/incoming/sync-toggle", { enabled })).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["incoming-sync-status"] }),
  });

  return {
    summary, summaryLoading, syncStatus, triage,
    isRefreshing: summaryFetching || triageFetching,
    relativeRefresh: relativeTime(triageUpdatedAt),
    sync, toggleAutoSync, syncError, lastSyncResult,
  };
}

export type IncomingLive = ReturnType<typeof useIncomingLive>;

export default function IncomingSection({ live }: { live: IncomingLive }) {
  const queryClient = useQueryClient();
  const {
    summary, summaryLoading: isLoading, triage, syncError, lastSyncResult,
  } = live;
  const [taskType, setTaskType] = useState<string | null>(null);
  // Collapsed by default — the full ticket list is long, so it only
  // renders once someone actually asks to see it.
  const [workQueueExpanded, setWorkQueueExpanded] = useState(false);
  const [taskStatus, setTaskStatus] = useState<"open" | "done" | "all">("open");
  const { data: taskSummary } = useQuery<TaskQueueSummary>({
    queryKey: ["incoming-tasks-summary"],
    queryFn: async () => (await api.get("/api/incoming/tasks-summary")).data,
  });
  const { data: queueTasks } = useQuery<QueueTask[]>({
    queryKey: ["incoming-tasks", taskStatus, taskType],
    queryFn: async () =>
      (await api.get("/api/incoming/tasks", {
        params: { status: taskStatus, ...(taskType ? { task_type: taskType } : {}) },
      })).data,
  });

  const { data: limitFwd } = useQuery<LimitForwardStatus>({
    queryKey: ["limit-forward-status"],
    queryFn: async () => (await api.get("/api/incoming/limit-forward-status")).data,
  });
  const toggleLimitFwd = useMutation({
    mutationFn: async (enabled: boolean) =>
      (await api.patch("/api/incoming/limit-forward-toggle", { enabled })).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["limit-forward-status"] }),
  });

  const setTaskState = useMutation({
    mutationFn: async ({ id, status }: { id: number; status: string }) =>
      (await api.patch(`/api/incoming/tasks/${id}`, { status })).data,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["incoming-tasks"] });
      queryClient.invalidateQueries({ queryKey: ["incoming-tasks-summary"] });
    },
  });

  const noiseTier = triage?.by_tier.find((t) => t.tier === "noise");
  // Chip counts must follow the status you're actually looking at — showing
  // open counts while viewing Done made the numbers contradict the rows.
  const countFor = (b: { open: number; done: number; dismissed: number }) =>
    taskStatus === "all" ? b.open + b.done + b.dismissed : b[taskStatus];

  return (
    <div>
      <p className="mb-5 max-w-3xl text-sm text-ink-500">
        SBI-domain incoming mail, tagged by what it needs from you and how much is being automated.
        Detection only — nothing here drafts, sends, files, or changes anything in Gmail.
      </p>

      {syncError && <div className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{syncError}</div>}
      {lastSyncResult && (
        <div className="mb-4 rounded-lg bg-info-soft border border-info-line px-3 py-2 text-xs text-info-fg">
          Scanned {lastSyncResult.ingest.scanned}, {lastSyncResult.ingest.new} new, {lastSyncResult.replies_updated} reply status(es) updated.
          {lastSyncResult.ingest.errors > 0 && ` ${lastSyncResult.ingest.errors} error(s).`}
        </div>
      )}

      {isLoading || !summary ? (
        <LoadingBlock />
      ) : (
        <>
          <div className="grid grid-cols-4 gap-4">
            <CountTile
              label="SBI Direct Mail" value={summary.direct_total} icon={Inbox} tone="brand"
              sub={`addressed to you · ${summary.cc_total} more cc'd`}
            />
            <CountTile
              label="Open Work Items" value={taskSummary?.totals.open ?? 0} icon={ListChecks} tone="amber"
              sub={
                taskSummary && taskSummary.totals.done > 0
                  ? `${taskSummary.totals.done} completed · Limit Approval Request`
                  : "Limit Approval Request tickets"
              }
            />
            <PercentTile
              label="SBI Reply Rate" percent={Math.round(summary.direct_reply_rate * 100)} icon={Reply}
              sub={`${summary.direct_replied} of ${summary.direct_total} direct replied`}
            />
            <PercentTile
              label="No Reply Needed" percent={noiseTier ? Math.round(noiseTier.pct * 100) : 0} icon={Sparkles}
              sub={noiseTier ? `${noiseTier.count} filtered out of your way` : "—"}
            />
          </div>

          {taskSummary && (taskSummary.totals.open > 0 || taskSummary.totals.done > 0) && (
            <div className="mt-6">
              <Card>
                <CardHeader
                  title="Work Queue"
                  subtitle="The actual units of work pulled out of task mail — one row per ticket number or CSP code, instead of one row per email. Marking something done updates this app only; it does not reply to or archive anything in Gmail."
                  action={
                    <div className="flex items-center gap-2">
                      <div className="flex items-center gap-1">
                        {(["open", "done", "all"] as const).map((s) => (
                          <button
                            key={s}
                            type="button"
                            onClick={() => setTaskStatus(s)}
                            className={`rounded-md px-2.5 py-1 text-xs font-medium capitalize transition-colors ${
                              taskStatus === s ? "bg-brand-600 text-white" : "text-ink-500 hover:bg-ink-100"
                            }`}
                          >
                            {s}
                          </button>
                        ))}
                      </div>
                      <button
                        type="button"
                        onClick={() => setWorkQueueExpanded((v) => !v)}
                        aria-label={workQueueExpanded ? "Collapse work queue" : "Expand work queue"}
                        className="flex h-7 w-7 items-center justify-center rounded-md text-ink-500 hover:bg-ink-100"
                      >
                        <ChevronDown
                          className={`h-4 w-4 transition-transform ${workQueueExpanded ? "rotate-180" : ""}`}
                          strokeWidth={2.25}
                        />
                      </button>
                    </div>
                  }
                />
                {workQueueExpanded && limitFwd && (
                  <div className="flex items-start justify-between gap-4 border-b border-ink-100 bg-ink-50/60 px-5 py-3">
                    <div className="text-xs text-ink-600">
                      <span className="font-medium text-ink-900">Auto-forward limit requests</span>
                      {" — "}
                      <span className="font-medium text-brand-700">sends</span> the forward directly to{" "}
                      <span className="font-mono">{limitFwd.forwardTo}</span> (cc{" "}
                      <span className="font-mono">{limitFwd.forwardCc}</span>) for each new limit request, no review step.
                      <div className="mt-0.5 text-ink-400">
                        Approving replies from {limitFwd.forwardTo.split("@")[0]} auto-close the matching ticket. Applies to mail arriving
                        {limitFwd.since ? ` after ${new Date(limitFwd.since).toLocaleString()}` : " after you switch it on"},
                        so the existing backlog is left alone.
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2 pt-0.5">
                      <Badge tone={limitFwd.enabled ? "green" : "slate"}>
                        {limitFwd.enabled ? "ON" : "OFF"}
                      </Badge>
                      <Toggle
                        checked={limitFwd.enabled}
                        onChange={() => toggleLimitFwd.mutate(!limitFwd.enabled)}
                        disabled={toggleLimitFwd.isPending}
                      />
                    </div>
                  </div>
                )}

                {workQueueExpanded && (
                <>
                <div className="border-b border-ink-100 px-5 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setTaskType(null)}
                      className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                        taskType === null ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-600 hover:bg-ink-200"
                      }`}
                    >
                      All {taskStatus} ({countFor(taskSummary.totals)})
                    </button>
                    {taskSummary.by_type
                      // Hide types with nothing in the CURRENT status view —
                      // a chip reading "(0)" is just noise to click through.
                      .filter((b) => countFor(b) > 0)
                      .map((b) => (
                        <button
                          key={b.taskType}
                          type="button"
                          onClick={() => setTaskType(b.taskType)}
                          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                            taskType === b.taskType ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-600 hover:bg-ink-200"
                          }`}
                        >
                          {b.taskType} ({countFor(b)})
                        </button>
                      ))}
                  </div>
                </div>

                {!queueTasks || queueTasks.length === 0 ? (
                  <EmptyState
                    icon={Sparkles}
                    title={taskStatus === "open" ? "Nothing open here" : "Nothing to show"}
                    subtitle={taskStatus === "open" ? "All caught up in this category." : "Try a different filter."}
                  />
                ) : (
                  <>
                    <Table>
                      <thead>
                        <tr><Th>ID</Th><Th>Type</Th><Th>Subject</Th><Th>From</Th><Th>Received</Th><Th>Action</Th></tr>
                      </thead>
                      <tbody>
                        {queueTasks.map((t) => (
                          <tr key={t.id}>
                            <Td className="whitespace-nowrap font-mono font-medium text-ink-900">
                              {t.identifier ?? <span className="font-sans text-ink-400">—</span>}
                            </Td>
                            <Td className="whitespace-nowrap text-ink-500">{t.taskType}</Td>
                            <Td className="max-w-[340px] truncate">{t.subject}</Td>
                            <Td className="max-w-[140px] truncate text-ink-500">{t.sender}</Td>
                            <Td className="whitespace-nowrap text-ink-500">{fmtDateTime(t.receivedAt)}</Td>
                            <Td className="whitespace-nowrap">
                              {t.status === "open" ? (
                                <div className="flex items-center gap-1">
                                  <Button
                                    size="sm"
                                    onClick={() => setTaskState.mutate({ id: t.id, status: "done" })}
                                    disabled={setTaskState.isPending}
                                  >
                                    Done
                                  </Button>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => setTaskState.mutate({ id: t.id, status: "dismissed" })}
                                    disabled={setTaskState.isPending}
                                  >
                                    Skip
                                  </Button>
                                </div>
                              ) : (
                                <div className="flex items-center gap-2">
                                  <Badge tone={t.status === "done" ? "green" : "slate"}>{t.status}</Badge>
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => setTaskState.mutate({ id: t.id, status: "open" })}
                                    disabled={setTaskState.isPending}
                                  >
                                    Reopen
                                  </Button>
                                </div>
                              )}
                            </Td>
                          </tr>
                        ))}
                      </tbody>
                    </Table>
                    <div className="border-t border-ink-100 px-5 py-3 text-xs text-ink-400">
                      Showing {queueTasks.length} task(s). One email can produce several tasks — a subject like
                      "…request 325588 &amp; 325589…" is genuinely two approvals.
                    </div>
                  </>
                )}
                </>
                )}
                {!workQueueExpanded && (
                  <button
                    type="button"
                    onClick={() => setWorkQueueExpanded(true)}
                    className="w-full px-5 py-3 text-left text-xs text-ink-500 hover:bg-ink-50"
                  >
                    {countFor(taskSummary.totals)} {taskStatus} ticket(s) — click the arrow to view
                  </button>
                )}
              </Card>
            </div>
          )}

          <div className="mt-6">
            <IncomingAckDashboard />
          </div>

          <div className="mt-6">
            <IncomingMailBreakdown />
          </div>
        </>
      )}
    </div>
  );
}
