import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Send, X, ChevronRight } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { Button, Card, CardHeader, Badge, EmptyState, LoadingBlock, Table, Th, Td } from "./ui";

// Same validated 4-hue set as OutgoingLevelDashboard/IncomingAckDashboard —
// reused rather than re-picked so every "Power BI" card in this app reads
// as one system.
const CATEGORY_COLOR: Record<string, string> = {
  "Performance-Based": "#c1520a",
  "Report Distribution": "#2452c0",
  "Issue Related": "#127a38",
  "Other": "#a8998a",
};
const CATEGORY_SOFT: Record<string, string> = {
  "Performance-Based": "bg-brand-50 border-brand-200 text-brand-700",
  "Report Distribution": "bg-info-soft border-info-line text-info-fg",
  "Issue Related": "bg-good-soft border-good-line text-good-fg",
  "Other": "bg-ink-100 border-ink-200 text-ink-500",
};
const CATEGORY_ORDER = ["Performance-Based", "Report Distribution", "Issue Related", "Other"];

interface CategoryRow { category: string; count: number; pct: number }
interface OutgoingMailSummary { total_outgoing: number; by_category: CategoryRow[] }
interface ReplyCategoryRow { category: string; total: number; replied: number; reply_rate: number }
interface OutgoingReplySummary { total_checked: number; total_replied: number; reply_rate: number; by_category: ReplyCategoryRow[] }
interface ScanResult { scanned: number; new: number; errors: number; error?: string; reply_backfilled?: number }
interface DetailRow { id: number; to: string; subject: string; category: string; sentAt: string | null; replied: boolean }
interface DetailResponse { total: number; page: number; pageSize: number; rows: DetailRow[] }

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true });
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: { payload: CategoryRow }[] }) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0].payload;
  return (
    <div className="rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 flex items-center gap-1.5 font-semibold text-ink-900">
        <span className="h-2 w-2 rounded-full" style={{ background: CATEGORY_COLOR[row.category] ?? "#a8998a" }} />
        {row.category}
      </div>
      <div className="text-ink-600">{row.count.toLocaleString()} email{row.count === 1 ? "" : "s"} ({Math.round(row.pct * 100)}%)</div>
    </div>
  );
}

function CategoryTile({
  row, replyRow, selected, onClick,
}: { row: CategoryRow; replyRow: ReplyCategoryRow | undefined; selected: boolean; onClick: () => void }) {
  const soft = CATEGORY_SOFT[row.category] ?? "border-ink-200 bg-white";
  return (
    <button
      onClick={onClick}
      className={`flex-1 rounded-xl border px-4 py-3.5 text-left transition-all ${
        selected ? `${soft} ring-2 ring-offset-1` : "border-ink-200 bg-white hover:border-ink-300 hover:shadow-sm"
      }`}
      style={selected ? { boxShadow: `0 0 0 2px ${CATEGORY_COLOR[row.category] ?? "#a8998a"}22` } : undefined}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ink-500">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: CATEGORY_COLOR[row.category] ?? "#a8998a" }} />
          {row.category}
        </span>
        <ChevronRight className={`h-3.5 w-3.5 shrink-0 text-ink-300 transition-transform ${selected ? "rotate-90" : ""}`} strokeWidth={2.25} />
      </div>
      <div className="mt-2 font-mono text-2xl font-semibold tabular-nums text-ink-900">{row.count.toLocaleString()}</div>
      <div className="mt-1 text-xs text-ink-500">
        {replyRow ? `${replyRow.replied}/${replyRow.total} replied (${Math.round(replyRow.reply_rate * 100)}%)` : `${Math.round(row.pct * 100)}% of outgoing`}
      </div>
    </button>
  );
}

function DrillDown({ categoryRow }: { categoryRow: CategoryRow }) {
  const [page, setPage] = useState(1);
  const pageSize = 8;

  const { data, isLoading } = useQuery<DetailResponse>({
    queryKey: ["outgoing-mail-detail", categoryRow.category, page],
    queryFn: async () => (
      await api.get("/api/incoming/outgoing-mail-detail", {
        params: { category: categoryRow.category, page, pageSize },
      })
    ).data,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;

  return (
    <div className="border-t border-ink-200 bg-ink-50/60 px-5 py-5">
      <div className="overflow-hidden rounded-lg border border-ink-200 bg-white">
        {isLoading ? (
          <LoadingBlock />
        ) : !data || data.rows.length === 0 ? (
          <EmptyState title="No emails in this category" subtitle="Try scanning sent mail again for a deeper slice." />
        ) : (
          <>
            <Table>
              <thead>
                <tr><Th>To</Th><Th>Subject</Th><Th>Sent</Th><Th>Replied</Th></tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.id}>
                    <Td className="max-w-[200px] truncate text-ink-600">{r.to}</Td>
                    <Td>
                      <div className="max-w-md truncate font-medium text-ink-900" title={r.subject}>{r.subject}</div>
                    </Td>
                    <Td className="whitespace-nowrap text-ink-500">{fmtDate(r.sentAt)}</Td>
                    <Td>{r.replied ? <Badge tone="green">Replied</Badge> : <span className="text-ink-300">—</span>}</Td>
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
          </>
        )}
      </div>
    </div>
  );
}

export default function OutgoingSentMailDashboard() {
  const queryClient = useQueryClient();
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  const [lastScan, setLastScan] = useState<ScanResult | null>(null);

  const { data, isLoading } = useQuery<OutgoingMailSummary>({
    queryKey: ["outgoing-mail-summary"],
    queryFn: async () => (await api.get("/api/incoming/outgoing-mail-summary")).data,
  });
  const { data: replyData } = useQuery<OutgoingReplySummary>({
    queryKey: ["outgoing-reply-summary"],
    queryFn: async () => (await api.get("/api/incoming/outgoing-reply-summary")).data,
  });

  const scan = useMutation({
    mutationFn: async () => (await api.post("/api/incoming/outgoing-mail-sync")).data as ScanResult,
    onSuccess: (res) => {
      setScanError(null);
      setLastScan(res);
      queryClient.invalidateQueries({ queryKey: ["outgoing-mail-summary"] });
      queryClient.invalidateQueries({ queryKey: ["outgoing-reply-summary"] });
      queryClient.invalidateQueries({ queryKey: ["outgoing-mail-detail"] });
    },
    onError: (err) => setScanError(apiErrorMessage(err, "Scan failed.")),
  });

  if (isLoading || !data) return <Card><LoadingBlock /></Card>;

  const chartData = [...data.by_category].sort(
    (a, b) => CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category),
  );
  const selected = selectedCategory ? data.by_category.find((c) => c.category === selectedCategory) ?? null : null;
  const replyFor = (cat: string) => replyData?.by_category.find((c) => c.category === cat);

  return (
    <Card>
      <CardHeader
        title="Outgoing Mail (Sent Folder)"
        subtitle="Everything sent from the connected account — via this app or manually — deeply scanned and classified by keyword. Click a category to see the actual emails."
        action={
          <Button size="sm" variant="secondary" onClick={() => scan.mutate()} disabled={scan.isPending}>
            <Send className={`h-3.5 w-3.5 ${scan.isPending ? "animate-pulse" : ""}`} strokeWidth={2.25} />
            {scan.isPending ? "Deep scanning…" : "Scan sent mail"}
          </Button>
        }
      />

      {scanError && <div className="mx-5 mt-4 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700">{scanError}</div>}
      {lastScan && (
        <div className="mx-5 mt-4 rounded-md border border-info-line bg-info-soft px-3 py-2 text-xs text-info-fg">
          Scanned {lastScan.scanned}, {lastScan.new} new.
          {lastScan.errors > 0 && ` ${lastScan.errors} error(s).`}
        </div>
      )}

      {data.total_outgoing === 0 ? (
        <EmptyState icon={Send} title="No sent mail scanned yet" subtitle="Click Scan sent mail to deeply scan and classify the connected account's Sent folder." />
      ) : (
        <>
          <div className="px-5 pt-4">
            <ResponsiveContainer width="100%" height={Math.max(140, chartData.length * 48)}>
              <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 44, bottom: 4, left: 4 }} barCategoryGap={16}>
                <XAxis type="number" hide />
                <YAxis
                  type="category" dataKey="category" width={140} tickLine={false} axisLine={false}
                  tick={{ fill: "#4b443d", fontSize: 12, fontWeight: 500 }}
                />
                <Tooltip cursor={{ fill: "#e9e2d9", opacity: 0.4 }} content={<ChartTooltip />} />
                <Bar
                  dataKey="count" fill="#c1520a" radius={[0, 4, 4, 0]} maxBarSize={24} cursor="pointer"
                  isAnimationActive={false}
                  onClick={(d: unknown) => {
                    const cat = (d as { category?: string })?.category;
                    if (cat) setSelectedCategory((cur) => (cur === cat ? null : cat));
                  }}
                  label={{ position: "right", fill: "#7a6f64", fontSize: 12, fontWeight: 600 }}
                >
                  {chartData.map((row) => (
                    <Cell
                      key={row.category}
                      fill={CATEGORY_COLOR[row.category] ?? "#a8998a"}
                      fillOpacity={selectedCategory && selectedCategory !== row.category ? 0.35 : 1}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="flex flex-wrap gap-3 px-5 pb-5 pt-2">
            {chartData.map((row) => (
              <CategoryTile
                key={row.category}
                row={row}
                replyRow={replyFor(row.category)}
                selected={selectedCategory === row.category}
                onClick={() => setSelectedCategory((cur) => (cur === row.category ? null : row.category))}
              />
            ))}
          </div>

          {selected && (
            <>
              <div className="flex items-center justify-between border-t border-ink-200 px-5 pt-3">
                <div className="flex items-center gap-2 py-2 text-sm font-semibold text-ink-800">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: CATEGORY_COLOR[selected.category] ?? "#a8998a" }} />
                  {selected.category} — detail
                </div>
                <button
                  onClick={() => setSelectedCategory(null)}
                  className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-ink-500 hover:bg-ink-100"
                >
                  <X className="h-3.5 w-3.5" strokeWidth={2.25} />Close
                </button>
              </div>
              <DrillDown key={selected.category} categoryRow={selected} />
            </>
          )}
        </>
      )}
    </Card>
  );
}
