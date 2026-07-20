import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, FlaskConical, Sheet as SheetIcon, Globe, Play, Square } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { PageHeader, Card, CardHeader, Button, Badge, Table, Th, Td, Spinner } from "../components/ui";

interface ScheduleRow {
  reportId: number; reportName: string; frequency: string | null; orgLevels: string[];
  hasSource: boolean; sourceId: number | null; isOn: boolean;
  fetchTime: string; sendTime: string;
  lastFetchAt: string | null; nextFetchAt: string | null;
  lastRunAt: string | null; nextRunAt: string | null;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

const FREQUENCY_TONE: Record<string, "blue" | "amber" | "green"> = { Daily: "blue", Weekly: "amber", Monthly: "green" };

function ConnectSourceForm({ reportId, onDone }: { reportId: number; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [sourceType, setSourceType] = useState<"Google Sheet" | "REST API">("Google Sheet");
  const [name, setName] = useState("");
  const [sheetUrl, setSheetUrl] = useState("");
  const [sheetTab, setSheetTab] = useState("Sheet1");
  const [baseUrl, setBaseUrl] = useState("");
  const [endpointPath, setEndpointPath] = useState("");
  const [filenameTemplate, setFilenameTemplate] = useState("Report_{date:%d-%m-%Y}.xlsx");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      const body: Record<string, unknown> = { name, reportId, sourceType, filenameTemplate };
      if (sourceType === "Google Sheet") {
        body.googleSheetUrl = sheetUrl;
        body.googleSheetTab = sheetTab;
      } else {
        body.baseUrl = baseUrl;
        body.endpointPath = endpointPath;
      }
      return (await api.post("/api/sources", body)).data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["schedules"] });
      onDone();
    },
    onError: (err) => setError(apiErrorMessage(err, "Couldn't connect that source.")),
  });

  return (
    <div className="rounded-lg border border-ink-200 bg-ink-50 p-4">
      <div className="mb-3 flex gap-2">
        {(["Google Sheet", "REST API"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setSourceType(t)}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              sourceType === t ? "bg-brand-600 text-white" : "bg-white text-ink-600 border border-ink-200"
            }`}
          >
            {t === "Google Sheet" ? <SheetIcon className="h-3.5 w-3.5" strokeWidth={2.25} /> : <Globe className="h-3.5 w-3.5" strokeWidth={2.25} />}
            {t}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="col-span-2">
          <label className="mb-1 block text-xs font-medium text-ink-500">Source Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Daily CSP Calling Sheet"
            className="w-full rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500" />
        </div>

        {sourceType === "Google Sheet" ? (
          <>
            <div className="col-span-2">
              <label className="mb-1 block text-xs font-medium text-ink-500">Google Sheet URL</label>
              <input value={sheetUrl} onChange={(e) => setSheetUrl(e.target.value)} placeholder="https://docs.google.com/spreadsheets/d/..."
                className="w-full rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500" />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-ink-500">Tab Name</label>
              <input value={sheetTab} onChange={(e) => setSheetTab(e.target.value)}
                className="w-full rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500" />
            </div>
          </>
        ) : (
          <>
            <div className="col-span-2">
              <label className="mb-1 block text-xs font-medium text-ink-500">Base URL</label>
              <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://internal-dashboard.company.com/api"
                className="w-full rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500" />
            </div>
            <div className="col-span-2">
              <label className="mb-1 block text-xs font-medium text-ink-500">Endpoint Path</label>
              <input value={endpointPath} onChange={(e) => setEndpointPath(e.target.value)} placeholder="/reports/daily?date={date:%Y-%m-%d}"
                className="w-full rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500" />
            </div>
          </>
        )}

        <div className="col-span-2">
          <label className="mb-1 block text-xs font-medium text-ink-500">Filename to Save As</label>
          <input value={filenameTemplate} onChange={(e) => setFilenameTemplate(e.target.value)}
            className="w-full rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500" />
        </div>
      </div>

      {error && <div className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}

      <div className="mt-3 flex gap-2">
        <Button size="sm" onClick={() => create.mutate()} disabled={create.isPending || !name.trim()}>
          <Link2 className="h-3.5 w-3.5" strokeWidth={2.25} />
          {create.isPending ? "Connecting..." : "Connect"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>Cancel</Button>
      </div>
    </div>
  );
}

export default function Scheduler() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery<ScheduleRow[]>({
    queryKey: ["schedules"],
    queryFn: async () => (await api.get("/api/schedules")).data,
  });
  const [connectingReportId, setConnectingReportId] = useState<number | null>(null);
  const [testingSourceId, setTestingSourceId] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<{ reportId: number; success: boolean; message: string } | null>(null);
  const [times, setTimes] = useState<Record<number, { fetchTime: string; sendTime: string }>>({});

  const toggle = useMutation({
    mutationFn: async ({ reportId, turnOn }: { reportId: number; turnOn: boolean }) => {
      if (!turnOn) return api.post(`/api/schedules/${reportId}/disable`);
      const t = times[reportId];
      return api.post(`/api/schedules/${reportId}/enable`, t ? { fetchTime: t.fetchTime, sendTime: t.sendTime } : {});
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["schedules"] }),
  });

  function timeFor(r: ScheduleRow) {
    return times[r.reportId] ?? { fetchTime: r.fetchTime, sendTime: r.sendTime };
  }
  function setTimeFor(reportId: number, field: "fetchTime" | "sendTime", value: string, row: ScheduleRow) {
    const current = times[reportId] ?? { fetchTime: row.fetchTime, sendTime: row.sendTime };
    setTimes({ ...times, [reportId]: { ...current, [field]: value } });
  }

  async function handleTestFetch(reportId: number, sourceId: number) {
    setTestingSourceId(sourceId);
    setTestResult(null);
    try {
      const res = await api.post(`/api/sources/${sourceId}/test-fetch`);
      setTestResult({ reportId, success: res.data.success, message: res.data.success ? `Downloaded "${res.data.fileName}"` : res.data.error });
    } catch (err) {
      setTestResult({ reportId, success: false, message: apiErrorMessage(err) });
    } finally {
      setTestingSourceId(null);
    }
  }

  if (isLoading) return null;

  return (
    <div>
      <PageHeader
        title="Scheduler"
        subtitle="Set a fetch and send time for a report and start it — it runs on its own from then on. Stop it any time."
      />
      <Card>
        <CardHeader title="Reports" />
        <Table>
          <thead>
            <tr><Th>Report</Th><Th>Frequency</Th><Th>Sent To</Th><Th>Source</Th><Th>Timing</Th></tr>
          </thead>
          <tbody>
            {(data ?? []).map((r) => (
              <Fragment key={r.reportId}>
                <tr>
                  <Td className="font-medium text-ink-900">{r.reportName}</Td>
                  <Td>{r.frequency && <Badge tone={FREQUENCY_TONE[r.frequency] ?? "slate"}>{r.frequency}</Badge>}</Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      {r.orgLevels.map((lvl) => <Badge key={lvl} tone="slate">{lvl}</Badge>)}
                    </div>
                  </Td>
                  <Td>
                    {r.hasSource ? (
                      <div className="flex items-center gap-2">
                        <Badge tone="green">Connected</Badge>
                        <button
                          onClick={() => handleTestFetch(r.reportId, r.sourceId!)}
                          disabled={testingSourceId === r.sourceId}
                          className="flex items-center gap-1 text-xs font-medium text-brand-600 hover:text-brand-700 disabled:opacity-50"
                        >
                          {testingSourceId === r.sourceId ? <Spinner className="h-3 w-3" /> : <FlaskConical className="h-3 w-3" strokeWidth={2.25} />}
                          Test fetch
                        </button>
                      </div>
                    ) : (
                      <Button size="sm" variant="secondary" onClick={() => setConnectingReportId(r.reportId)}>
                        <Link2 className="h-3.5 w-3.5" strokeWidth={2.25} />
                        Connect source
                      </Button>
                    )}
                  </Td>
                  <Td>
                    {r.isOn ? (
                      <div className="flex items-center gap-3">
                        <div className="text-xs text-ink-600">
                          <div>Fetch {r.fetchTime} · next {fmtTime(r.nextFetchAt)}</div>
                          <div>Send {r.sendTime} · next {fmtTime(r.nextRunAt)}</div>
                        </div>
                        <button
                          onClick={() => toggle.mutate({ reportId: r.reportId, turnOn: false })}
                          disabled={toggle.isPending}
                          className="flex shrink-0 items-center gap-1 rounded-md border border-ink-200 px-2 py-1 text-xs font-medium text-ink-600 hover:bg-ink-50 disabled:opacity-50"
                        >
                          <Square className="h-3 w-3" strokeWidth={2.25} />
                          Stop
                        </button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-1.5 text-xs">
                        <span className="text-ink-500">Fetch</span>
                        <input
                          type="time"
                          value={timeFor(r).fetchTime}
                          onChange={(e) => setTimeFor(r.reportId, "fetchTime", e.target.value, r)}
                          className="rounded border border-ink-300 px-1.5 py-1 text-xs"
                        />
                        <span className="text-ink-500">Send</span>
                        <input
                          type="time"
                          value={timeFor(r).sendTime}
                          onChange={(e) => setTimeFor(r.reportId, "sendTime", e.target.value, r)}
                          className="rounded border border-ink-300 px-1.5 py-1 text-xs"
                        />
                        <button
                          onClick={() => toggle.mutate({ reportId: r.reportId, turnOn: true })}
                          disabled={!r.hasSource || toggle.isPending}
                          title={r.hasSource ? "Start" : "Connect a source first"}
                          className="flex shrink-0 items-center gap-1 rounded-md bg-brand-600 px-2 py-1 text-xs font-medium text-white hover:bg-brand-700 disabled:opacity-40"
                        >
                          <Play className="h-3 w-3" strokeWidth={2.25} />
                          Start
                        </button>
                      </div>
                    )}
                  </Td>
                </tr>
                {connectingReportId === r.reportId && (
                  <tr>
                    <td colSpan={5} className="border-b border-ink-50 px-5 py-4">
                      <ConnectSourceForm reportId={r.reportId} onDone={() => setConnectingReportId(null)} />
                    </td>
                  </tr>
                )}
                {testResult?.reportId === r.reportId && (
                  <tr>
                    <td colSpan={5} className="px-5 pb-3">
                      <div className={`rounded-md px-3 py-2 text-xs ${testResult.success ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"}`}>
                        {testResult.message}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </Table>
      </Card>
    </div>
  );
}
