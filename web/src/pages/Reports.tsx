import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, CheckCircle2, XCircle } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { PageHeader, Card, CardHeader, EmptyState, Table, Th, Td, Badge, Button } from "../components/ui";

interface ReportItem {
  id: number; reportName: string; description: string | null; frequency: string | null;
  orgLevels: string[]; isActive: boolean; templateId: number | null; templateName: string | null;
  deliveryMode: "draft" | "send";
}

interface RecipientOption {
  source: "sheet" | "org"; unitId: number | null; name: string; level: string; email: string;
  ccEmails: string | null;
}

interface TestSendResult {
  jobId: number; sentTo: string; ccTo: string | null; status: string; sentVia: string | null; error: string | null;
}

function TestSendBar({ reports }: { reports: ReportItem[] }) {
  const [reportId, setReportId] = useState<number | "">("");
  const [recipientIndex, setRecipientIndex] = useState<number | "">("");
  const [useTestAddress, setUseTestAddress] = useState(false);
  const [testEmail, setTestEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TestSendResult | null>(null);

  const { data: recipients } = useQuery<RecipientOption[]>({
    queryKey: ["report-recipients", reportId],
    queryFn: async () => (await api.get(`/api/reports/${reportId}/recipients`)).data,
    enabled: reportId !== "",
  });

  const selectedReport = reports.find((r) => r.id === reportId);
  const selectedRecipient = recipientIndex !== "" ? recipients?.[recipientIndex] : undefined;

  const send = useMutation({
    mutationFn: async () => {
      if (!selectedRecipient) throw new Error("Pick a recipient first.");
      const body: Record<string, unknown> = {
        source: selectedRecipient.source, level: selectedRecipient.level,
        unitId: selectedRecipient.unitId, name: selectedRecipient.name,
      };
      if (useTestAddress) body.overrideEmail = testEmail;
      return (await api.post(`/api/reports/${reportId}/test-send`, body)).data as TestSendResult;
    },
    onSuccess: (data) => {
      setResult(data);
      setError(null);
    },
    onError: (err) => {
      setResult(null);
      setError(apiErrorMessage(err, "Test send failed."));
    },
  });

  const canSend = reportId !== "" && recipientIndex !== "" && (!useTestAddress || testEmail.trim().length > 0);

  return (
    <Card className="mb-5">
      <div className="flex items-center gap-2.5 border-b border-ink-100 px-5 py-4">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-amber-50 text-amber-600">
          <FlaskConical className="h-4 w-4" strokeWidth={2.25} />
        </div>
        <div>
          <div className="text-sm font-semibold text-ink-900">Test Send</div>
          <div className="text-xs text-ink-500">Send one report to one recipient right now, without waiting for the schedule.</div>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-3 px-5 py-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-ink-500">Report</label>
          <select
            value={reportId}
            onChange={(e) => { setReportId(e.target.value ? Number(e.target.value) : ""); setRecipientIndex(""); setResult(null); setError(null); }}
            className="w-56 rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            <option value="">Choose a report...</option>
            {reports.map((r) => <option key={r.id} value={r.id}>{r.reportName}</option>)}
          </select>
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-ink-500">Recipient</label>
          <select
            value={recipientIndex}
            onChange={(e) => setRecipientIndex(e.target.value ? Number(e.target.value) : "")}
            disabled={reportId === ""}
            className="w-64 rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 disabled:bg-ink-50"
          >
            <option value="">
              {reportId === "" ? "Pick a report first" : recipients?.length ? "Choose a recipient..." : "No recipients configured for this report"}
            </option>
            {recipients?.map((r, i) => (
              <option key={`${r.source}-${r.unitId ?? r.name}`} value={i}>{r.name} ({r.level}) — {r.email}</option>
            ))}
          </select>
        </div>

        <div className="flex items-center gap-2 pb-2">
          <input
            id="use-test-address" type="checkbox" checked={useTestAddress}
            onChange={(e) => setUseTestAddress(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-ink-300"
          />
          <label htmlFor="use-test-address" className="text-xs font-medium text-ink-600">Redirect to a test address</label>
        </div>

        {useTestAddress && (
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-500">Test email</label>
            <input
              value={testEmail} onChange={(e) => setTestEmail(e.target.value)} placeholder="you@example.com"
              className="w-56 rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
            />
          </div>
        )}

        <Button size="sm" onClick={() => send.mutate()} disabled={!canSend || send.isPending}>
          <FlaskConical className="h-3.5 w-3.5" strokeWidth={2.25} />
          {send.isPending ? "Sending..." : `Send Test${selectedReport?.deliveryMode === "draft" ? " (Draft)" : ""}`}
        </Button>
      </div>

      {selectedRecipient?.ccEmails && (
        <div className="mx-5 mb-4 rounded-lg bg-sky-50 px-3 py-2 text-xs text-sky-800">
          This recipient normally CCs: <span className="font-medium">{selectedRecipient.ccEmails}</span>.
          {useTestAddress ? " Suppressed for this test send — only the test address above will receive it." : " This test send will also CC them."}
        </div>
      )}

      {error && (
        <div className="mx-5 mb-4 flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
          <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={2.25} />
          {error}
        </div>
      )}
      {result && (
        <div className="mx-5 mb-4 flex items-start gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={2.25} />
          <div>
            {result.sentVia === "gmail_draft"
              ? <>Draft created in Gmail, addressed to <span className="font-medium">{result.sentTo}</span>{result.ccTo ? <> (CC: {result.ccTo})</> : ""} — nothing was sent.</>
              : <>Actually sent to <span className="font-medium">{result.sentTo}</span>{result.ccTo ? <> (CC: {result.ccTo})</> : ""}{result.sentVia ? ` via ${result.sentVia}` : ""}.</>}
            {result.error && <div className="mt-0.5 text-amber-700">{result.error}</div>}
          </div>
        </div>
      )}
    </Card>
  );
}

function DeliveryModeControl({ report }: { report: ReportItem }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const setMode = useMutation({
    mutationFn: async (mode: "draft" | "send") =>
      (await api.patch(`/api/reports/${report.id}/delivery-mode`, { mode })).data,
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["reports"] });
    },
    onError: (err) => setError(apiErrorMessage(err, "Couldn't update this.")),
  });

  return (
    <div>
      <div className="inline-flex rounded-md border border-ink-200 p-0.5">
        {(["draft", "send"] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMode.mutate(m)}
            disabled={setMode.isPending}
            className={`rounded px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50 ${
              report.deliveryMode === m ? "bg-brand-600 text-white" : "text-ink-600 hover:bg-ink-100"
            }`}
          >
            {m === "draft" ? "Draft Only" : "Send Directly"}
          </button>
        ))}
      </div>
      {error && <div className="mt-1 text-xs text-rose-600">{error}</div>}
    </div>
  );
}

const FREQUENCY_TONE: Record<string, "blue" | "amber" | "green"> = {
  Daily: "blue", Weekly: "amber", Monthly: "green",
};

function ReportMappingTable() {
  const { data, isLoading } = useQuery<ReportItem[]>({
    queryKey: ["reports"],
    queryFn: async () => (await api.get("/api/reports")).data,
  });

  if (isLoading) return null;

  return (
    <Card>
      <CardHeader title="Report Mapping" subtitle="Which levels each report goes to, at what frequency, and with which template." />
      {!data || data.length === 0 ? (
        <EmptyState title="No report types configured yet" />
      ) : (
        <Table>
          <thead>
            <tr><Th>Report</Th><Th>Frequency</Th><Th>Sent To</Th><Th>Template</Th><Th>Delivery</Th></tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <tr key={r.id}>
                <Td className="font-medium text-ink-900">{r.reportName}</Td>
                <Td>{r.frequency ? <Badge tone={FREQUENCY_TONE[r.frequency] ?? "slate"}>{r.frequency}</Badge> : "—"}</Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    {r.orgLevels.map((lvl) => <Badge key={lvl} tone="slate">{lvl}</Badge>)}
                  </div>
                </Td>
                <Td>{r.templateName ?? <span className="text-ink-400">Not attached</span>}</Td>
                <Td><DeliveryModeControl report={r} /></Td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

export default function Reports() {
  const { data: reports } = useQuery<ReportItem[]>({
    queryKey: ["reports"],
    queryFn: async () => (await api.get("/api/reports")).data,
  });

  return (
    <div>
      <PageHeader title="Reports" subtitle="What each report sends, fully automated from the calling sheet." />

      {reports && reports.length > 0 && <TestSendBar reports={reports} />}

      <ReportMappingTable />
    </div>
  );
}
