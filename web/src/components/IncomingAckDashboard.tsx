import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { PenLine, Clock, UserCheck, X, ChevronRight } from "lucide-react";
import { api } from "../lib/api";
import { Card, CardHeader, Badge, EmptyState, LoadingBlock, Table, Th, Td } from "./ui";

// Same validated categorical palette as OutgoingLevelDashboard (dataviz
// skill's validate_palette.js — lightness band, chroma floor, CVD adjacent
// separation and contrast all pass for this light-mode surface). Reused
// rather than re-picked so the two "Power BI" dashboards read as one system.
// 5-slot categorical palette, dataviz-skill validated (lightness band,
// chroma floor, CVD adjacent separation, contrast all pass at
// node scripts/validate_palette.js — light mode, this card's surface).
const CATEGORY_COLOR: Record<string, string> = {
  "SBI Data / Status Push": "#c1520a",
  "Micro ATM Report": "#2452c0",
  "BC-CSP Agreement & PVR Pendency Report": "#127a38",
  "Passbook Printer Report": "#b98811",
  "BC Commission Report": "#8a3d9e",
};
const CATEGORY_SOFT: Record<string, string> = {
  "SBI Data / Status Push": "bg-brand-50 border-brand-200 text-brand-700",
  "Micro ATM Report": "bg-info-soft border-info-line text-info-fg",
  "BC-CSP Agreement & PVR Pendency Report": "bg-good-soft border-good-line text-good-fg",
  "Passbook Printer Report": "bg-warn-soft border-warn-line text-warn-fg",
  "BC Commission Report": "border-[#e2ccec] bg-[#f6ecf9] text-[#8a3d9e]",
};
const CATEGORY_SHORT: Record<string, string> = {
  "SBI Data / Status Push": "SBI Status Push",
  "Micro ATM Report": "Micro ATM Report",
  "BC-CSP Agreement & PVR Pendency Report": "BC-CSP Agreement / PVR",
  "Passbook Printer Report": "Passbook Printer",
  "BC Commission Report": "BC Commission Report",
};

interface CategoryRow {
  category: string; total: number; drafted: number; repliedByHuman: number; pending: number;
}
interface AckSummary {
  totalAcrossCategories: number; totalDrafted: number; totalPending: number;
  totalRepliedByHuman: number; categories: CategoryRow[]; skippedNotSbi: number;
}
interface DetailRow {
  id: number; sender: string; subject: string; category: string;
  receivedAt: string | null; status: "drafted" | "pending" | "repliedByHuman"; ackDraftId: string | null;
}
interface DetailResponse { total: number; page: number; pageSize: number; rows: DetailRow[] }

const STATUS_META: Record<string, { label: string; tone: "green" | "amber" | "blue" }> = {
  drafted: { label: "Drafted", tone: "amber" },
  pending: { label: "Pending", tone: "blue" },
  repliedByHuman: { label: "Human replied", tone: "green" },
};

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
        {CATEGORY_SHORT[row.category] ?? row.category}
      </div>
      <div className="text-ink-600">{row.total.toLocaleString()} email{row.total === 1 ? "" : "s"}</div>
      <div className="mt-1 text-ink-500">{row.drafted} drafted · {row.pending} pending</div>
    </div>
  );
}

function CategoryTile({
  row, selected, onClick,
}: { row: CategoryRow; selected: boolean; onClick: () => void }) {
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
          {CATEGORY_SHORT[row.category] ?? row.category}
        </span>
        <ChevronRight className={`h-3.5 w-3.5 shrink-0 text-ink-300 transition-transform ${selected ? "rotate-90" : ""}`} strokeWidth={2.25} />
      </div>
      <div className="mt-2 font-mono text-2xl font-semibold tabular-nums text-ink-900">{row.total.toLocaleString()}</div>
      <div className="mt-1 text-xs text-ink-500">
        {row.total === 0 ? "no mail matched" : `${row.drafted} drafted · ${row.pending} pending`}
      </div>
    </button>
  );
}

function DrillDown({ categoryRow }: { categoryRow: CategoryRow }) {
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 8;

  const { data, isLoading } = useQuery<DetailResponse>({
    queryKey: ["ack-detail", categoryRow.category, statusFilter, page],
    queryFn: async () => (
      await api.get("/api/incoming/ack-detail", {
        params: { category: categoryRow.category, status: statusFilter ?? undefined, page, pageSize },
      })
    ).data,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;
  const soft = CATEGORY_SOFT[categoryRow.category] ?? "border-ink-200 bg-white";

  const statusPills: { key: string | null; label: string; count: number }[] = [
    { key: null, label: `All (${categoryRow.total})`, count: categoryRow.total },
    { key: "pending", label: `Pending (${categoryRow.pending})`, count: categoryRow.pending },
    { key: "drafted", label: `Drafted (${categoryRow.drafted})`, count: categoryRow.drafted },
    { key: "repliedByHuman", label: `Human replied (${categoryRow.repliedByHuman})`, count: categoryRow.repliedByHuman },
  ];

  return (
    <div className="border-t border-ink-200 bg-ink-50/60 px-5 py-5">
      <div className="grid grid-cols-4 gap-3">
        <div className="rounded-lg border border-ink-200 bg-white px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
            Total
          </div>
          <div className="mt-1 font-mono text-lg font-semibold text-ink-900">{categoryRow.total.toLocaleString()}</div>
        </div>
        <div className="rounded-lg border border-ink-200 bg-white px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
            <Clock className="h-3 w-3" strokeWidth={2.25} />Pending
          </div>
          <div className="mt-1 font-mono text-lg font-semibold text-ink-900">{categoryRow.pending.toLocaleString()}</div>
        </div>
        <div className="rounded-lg border border-ink-200 bg-white px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
            <PenLine className="h-3 w-3" strokeWidth={2.25} />Drafted
          </div>
          <div className="mt-1 font-mono text-lg font-semibold text-ink-900">{categoryRow.drafted.toLocaleString()}</div>
        </div>
        <div className="rounded-lg border border-ink-200 bg-white px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
            <UserCheck className="h-3 w-3" strokeWidth={2.25} />Human replied
          </div>
          <div className="mt-1 font-mono text-lg font-semibold text-ink-900">{categoryRow.repliedByHuman.toLocaleString()}</div>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-1.5">
        <span className="text-xs font-medium text-ink-500">Filter:</span>
        {statusPills.map((p) => (
          <button
            key={p.label}
            onClick={() => { setStatusFilter(p.key); setPage(1); }}
            className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
              statusFilter === p.key ? `${soft} border` : "border-ink-200 bg-white text-ink-600 hover:border-ink-300"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div className="mt-4 overflow-hidden rounded-lg border border-ink-200 bg-white">
        {isLoading ? (
          <LoadingBlock />
        ) : !data || data.rows.length === 0 ? (
          <EmptyState title="No emails in this filter" subtitle="Try a different status above." />
        ) : (
          <>
            <Table>
              <thead>
                <tr><Th>Sender</Th><Th>Subject</Th><Th>Received</Th><Th>Status</Th></tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.id}>
                    <Td className="text-ink-600">{r.sender}</Td>
                    <Td>
                      <div className="max-w-md truncate font-medium text-ink-900" title={r.subject}>{r.subject}</div>
                    </Td>
                    <Td className="text-ink-500">{fmtDate(r.receivedAt)}</Td>
                    <Td><Badge tone={STATUS_META[r.status].tone}>{STATUS_META[r.status].label}</Badge></Td>
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

export default function IncomingAckDashboard() {
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);

  const { data, isLoading } = useQuery<AckSummary>({
    queryKey: ["ack-summary"],
    queryFn: async () => (await api.get("/api/incoming/ack-summary")).data,
    refetchInterval: 45_000,
  });

  if (isLoading || !data) return <Card><LoadingBlock /></Card>;

  const chartData = data.categories;
  const selected = selectedCategory ? data.categories.find((c) => c.category === selectedCategory) ?? null : null;

  return (
    <Card>
      <CardHeader
        title="Acknowledgment Automation"
        subtitle="SBI status-push mail matched for auto-acknowledgment — click a category to see which emails, and their draft status."
      />

      {data.totalAcrossCategories === 0 ? (
        <EmptyState title="No matching mail yet" subtitle="These categories fill in as incoming SBI status-push mail is synced and classified." />
      ) : (
        <>
          <div className="px-5 pt-4">
            <ResponsiveContainer width="100%" height={Math.max(140, chartData.length * 48)}>
              <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 36, bottom: 4, left: 4 }} barCategoryGap={16}>
                <XAxis type="number" hide />
                <YAxis
                  type="category"
                  dataKey={(row: CategoryRow) => CATEGORY_SHORT[row.category] ?? row.category}
                  width={170} tickLine={false} axisLine={false}
                  tick={{ fill: "#4b443d", fontSize: 12, fontWeight: 500 }}
                />
                <Tooltip cursor={{ fill: "#e9e2d9", opacity: 0.4 }} content={<ChartTooltip />} />
                <Bar
                  dataKey="total" fill="#c1520a" radius={[0, 4, 4, 0]} maxBarSize={24} cursor="pointer"
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
                  {CATEGORY_SHORT[selected.category] ?? selected.category} — detail
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
