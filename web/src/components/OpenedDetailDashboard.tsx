import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { MailOpen, MousePointerClick, Send, X } from "lucide-react";
import { api } from "../lib/api";
import { Card, CardHeader, Badge, EmptyState, LoadingBlock, Table, Th, Td } from "./ui";

interface OpenedRow {
  id: number; recipientName: string; recipientEmail: string; level: string; report: string;
  sentAt: string | null; opened: boolean; openedAt: string | null; openCount: number;
  detailOpened: boolean; detailOpenedAt: string | null; detailOpenCount: number;
}
interface OpenedDetailResponse {
  window: string; total: number; opened: number; detailOpened: number;
  page: number; pageSize: number; rows: OpenedRow[];
}

const WINDOWS: { value: string; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "all", label: "All time" },
];

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true });
}

export default function OpenedDetailDashboard({ onClose }: { onClose: () => void }) {
  const [window, setWindowValue] = useState("today");
  const [page, setPage] = useState(1);
  const pageSize = 10;

  const { data, isLoading } = useQuery<OpenedDetailResponse>({
    queryKey: ["opened-detail", window, page],
    queryFn: async () => (
      await api.get("/api/dashboard/opened-detail", { params: { window, page, pageSize } })
    ).data,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;
  const openRate = data && data.total > 0 ? Math.round((data.opened / data.total) * 100) : 0;
  const detailRate = data && data.total > 0 ? Math.round((data.detailOpened / data.total) * 100) : 0;

  return (
    <Card className="mt-4">
      <CardHeader
        title="Who Opened Their Mail"
        subtitle="Per-recipient open status, plus whether they clicked through to a CSP-wise detail card."
        action={
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-0.5 rounded-lg border border-ink-200 bg-ink-50 p-0.5">
              {WINDOWS.map((w) => (
                <button
                  key={w.value}
                  onClick={() => { setWindowValue(w.value); setPage(1); }}
                  className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                    window === w.value ? "bg-white text-brand-700 shadow-sm" : "text-ink-500 hover:text-ink-800"
                  }`}
                >
                  {w.label}
                </button>
              ))}
            </div>
            <button
              onClick={onClose}
              className="flex items-center gap-1 rounded-md px-2 py-1.5 text-xs font-medium text-ink-500 hover:bg-ink-100"
            >
              <X className="h-3.5 w-3.5" strokeWidth={2.25} />Close
            </button>
          </div>
        }
      />

      {isLoading || !data ? (
        <LoadingBlock />
      ) : data.total === 0 ? (
        <EmptyState title="No mail sent in this window" subtitle="Try a wider time window above." />
      ) : (
        <>
          <div className="grid grid-cols-3 gap-3 px-5 pt-4">
            <div className="rounded-lg border border-ink-200 bg-white px-3 py-2.5">
              <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
                <Send className="h-3 w-3" strokeWidth={2.25} />Sent
              </div>
              <div className="mt-1 font-mono text-lg font-semibold text-ink-900">{data.total.toLocaleString()}</div>
            </div>
            <div className="rounded-lg border border-brand-300 bg-brand-50 px-3 py-2.5 ring-1 ring-brand-200">
              <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-brand-700">
                <MousePointerClick className="h-3 w-3" strokeWidth={2.25} />Detail Card Clicked
              </div>
              <div className="mt-1 font-mono text-lg font-semibold text-brand-900">
                {data.detailOpened.toLocaleString()} <span className="text-xs font-normal text-brand-600">({detailRate}%)</span>
              </div>
              <div className="mt-0.5 text-[10px] text-brand-600">the reliable signal — a real click, not just a loaded pixel</div>
            </div>
            <div className="rounded-lg border border-ink-200 bg-white px-3 py-2.5">
              <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
                <MailOpen className="h-3 w-3" strokeWidth={2.25} />Opened (pixel)
              </div>
              <div className="mt-1 font-mono text-lg font-semibold text-ink-900">
                {data.opened.toLocaleString()} <span className="text-xs font-normal text-ink-400">({openRate}%)</span>
              </div>
            </div>
          </div>

          <div className="mx-5 mt-4 mb-5 overflow-hidden rounded-lg border border-ink-200 bg-white">
            <Table>
              <thead>
                <tr>
                  <Th>Recipient</Th><Th>Level</Th><Th>Report</Th><Th>Sent</Th><Th>Detail Card</Th><Th>Opened (pixel)</Th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.id}>
                    <Td>
                      <div className="font-medium text-ink-900">{r.recipientName}</div>
                      <div className="text-xs text-ink-400">{r.recipientEmail}</div>
                    </Td>
                    <Td className="text-ink-600">{r.level}</Td>
                    <Td className="text-ink-600">{r.report}</Td>
                    <Td className="text-ink-500">{fmtDate(r.sentAt)}</Td>
                    <Td>
                      {r.detailOpened ? (
                        <Badge tone="green">{fmtDate(r.detailOpenedAt)}</Badge>
                      ) : (
                        <span className="text-ink-300">Not clicked</span>
                      )}
                    </Td>
                    <Td>
                      {r.opened ? (
                        <Badge tone="blue">{fmtDate(r.openedAt)}</Badge>
                      ) : (
                        <span className="text-ink-300">—</span>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </Table>
            {totalPages > 1 && (
              <div className="flex items-center justify-between border-t border-ink-200 px-4 py-2.5 text-xs text-ink-500">
                <span>Page {page} of {totalPages} · {data.total.toLocaleString()} total</span>
                <div className="flex gap-1.5">
                  <button
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1}
                    className="rounded-md border border-ink-200 px-2.5 py-1 font-medium hover:bg-ink-50 disabled:opacity-40"
                  >
                    Prev
                  </button>
                  <button
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages}
                    className="rounded-md border border-ink-200 px-2.5 py-1 font-medium hover:bg-ink-50 disabled:opacity-40"
                  >
                    Next
                  </button>
                </div>
              </div>
            )}
          </div>
        </>
      )}
    </Card>
  );
}
