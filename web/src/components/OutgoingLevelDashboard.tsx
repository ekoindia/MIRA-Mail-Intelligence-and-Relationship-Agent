import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  Send, PenLine, AlertTriangle, MailOpen, Layers, X, ChevronRight,
} from "lucide-react";
import { api } from "../lib/api";
import { Card, CardHeader, Badge, EmptyState, LoadingBlock, Table, Th, Td } from "./ui";

// Fixed per-level color, validated as a categorical set (light-mode surface
// #fcfcfb) with scripts/validate_palette.js from the dataviz skill: lightness
// band, chroma floor, CVD adjacent-pair separation and contrast all pass.
// AO is intentionally NOT a strong hue — it currently carries ~0 volume, so
// per the skill's "9th series folds into Other" guidance it's a muted
// neutral rather than a 5th competing hue.
const LEVEL_COLOR: Record<string, string> = {
  RBO: "#c1520a",
  LHO: "#2452c0",
  Branch: "#127a38",
  "Corporate Center": "#b98811",
  AO: "#a8998a",
};
const LEVEL_SOFT: Record<string, string> = {
  RBO: "bg-brand-50 border-brand-200 text-brand-700",
  LHO: "bg-info-soft border-info-line text-info-fg",
  Branch: "bg-good-soft border-good-line text-good-fg",
  "Corporate Center": "bg-warn-soft border-warn-line text-warn-fg",
  AO: "bg-ink-100 border-ink-200 text-ink-500",
};

interface LevelRow {
  level: string; total: number; sent: number; drafted: number; failed: number;
  opened: number; openRate: number;
}
interface ByLevelReport { level: string; report: string; count: number }
interface OutgoingByLevel { window: string; totalAcrossLevels: number; levels: LevelRow[]; byLevelAndReport: ByLevelReport[] }
interface DetailRow {
  id: number; recipientName: string; recipientEmail: string; level: string; report: string;
  status: string; isDraft: boolean; createdAt: string; sentAt: string | null; opened: boolean; openedAt: string | null;
}
interface DetailResponse { total: number; page: number; pageSize: number; rows: DetailRow[] }

const WINDOWS: { value: string; label: string }[] = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
  { value: "all", label: "All time" },
];

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true });
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: { payload: LevelRow }[] }) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0].payload;
  return (
    <div className="rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 flex items-center gap-1.5 font-semibold text-ink-900">
        <span className="h-2 w-2 rounded-full" style={{ background: LEVEL_COLOR[row.level] ?? "#a8998a" }} />
        {row.level}
      </div>
      <div className="text-ink-600">{row.total.toLocaleString()} email{row.total === 1 ? "" : "s"}</div>
      <div className="mt-1 space-y-0.5 text-ink-500">
        <div>{row.sent.toLocaleString()} sent · {row.drafted.toLocaleString()} drafted{row.failed ? ` · ${row.failed} failed` : ""}</div>
      </div>
    </div>
  );
}

function LevelTile({
  row, selected, onClick,
}: { row: LevelRow; selected: boolean; onClick: () => void }) {
  const soft = LEVEL_SOFT[row.level] ?? LEVEL_SOFT.AO;
  return (
    <button
      onClick={onClick}
      className={`flex-1 rounded-xl border px-4 py-3.5 text-left transition-all ${
        selected ? `${soft} ring-2 ring-offset-1` : "border-ink-200 bg-white hover:border-ink-300 hover:shadow-sm"
      }`}
      style={selected ? { boxShadow: `0 0 0 2px ${LEVEL_COLOR[row.level] ?? "#a8998a"}22` } : undefined}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ink-500">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: LEVEL_COLOR[row.level] ?? "#a8998a" }} />
          {row.level}
        </span>
        <ChevronRight className={`h-3.5 w-3.5 shrink-0 text-ink-300 transition-transform ${selected ? "rotate-90" : ""}`} strokeWidth={2.25} />
      </div>
      <div className="mt-2 font-mono text-2xl font-semibold tabular-nums text-ink-900">{row.total.toLocaleString()}</div>
      <div className="mt-1 text-xs text-ink-500">
        {row.total === 0 ? "no mail in this window" : `${row.sent} sent · ${row.drafted} drafted`}
      </div>
    </button>
  );
}

function DrillDown({
  levelRow, reportBreakdown, window,
}: { levelRow: LevelRow; reportBreakdown: ByLevelReport[]; window: string }) {
  const [reportFilter, setReportFilter] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 8;

  const { data, isLoading } = useQuery<DetailResponse>({
    queryKey: ["outgoing-detail", levelRow.level, reportFilter, window, page],
    queryFn: async () => (
      await api.get("/api/dashboard/outgoing-detail", {
        params: { level: levelRow.level, report: reportFilter ?? undefined, window, page, pageSize },
      })
    ).data,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;
  const soft = LEVEL_SOFT[levelRow.level] ?? LEVEL_SOFT.AO;

  return (
    <div className="border-t border-ink-200 bg-ink-50/60 px-5 py-5">
      {/* Sub-KPIs for the selected level */}
      <div className="grid grid-cols-5 gap-3">
        <div className="rounded-lg border border-ink-200 bg-white px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
            <Layers className="h-3 w-3" strokeWidth={2.25} />Total
          </div>
          <div className="mt-1 font-mono text-lg font-semibold text-ink-900">{levelRow.total.toLocaleString()}</div>
        </div>
        <div className="rounded-lg border border-ink-200 bg-white px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
            <Send className="h-3 w-3" strokeWidth={2.25} />Sent
          </div>
          <div className="mt-1 font-mono text-lg font-semibold text-ink-900">{levelRow.sent.toLocaleString()}</div>
        </div>
        <div className="rounded-lg border border-ink-200 bg-white px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
            <PenLine className="h-3 w-3" strokeWidth={2.25} />Drafted
          </div>
          <div className="mt-1 font-mono text-lg font-semibold text-ink-900">{levelRow.drafted.toLocaleString()}</div>
        </div>
        <div className="rounded-lg border border-ink-200 bg-white px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
            <AlertTriangle className="h-3 w-3" strokeWidth={2.25} />Failed
          </div>
          <div className="mt-1 font-mono text-lg font-semibold text-ink-900">{levelRow.failed.toLocaleString()}</div>
        </div>
        <div className="rounded-lg border border-ink-200 bg-white px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-500">
            <MailOpen className="h-3 w-3" strokeWidth={2.25} />Opened
          </div>
          <div className="mt-1 font-mono text-lg font-semibold text-ink-900">{Math.round(levelRow.openRate * 100)}%</div>
        </div>
      </div>

      {/* By-report pills — click to further filter the table below */}
      {reportBreakdown.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-medium text-ink-500">By report:</span>
          <button
            onClick={() => { setReportFilter(null); setPage(1); }}
            className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
              reportFilter === null ? "border-ink-800 bg-ink-800 text-white" : "border-ink-200 bg-white text-ink-600 hover:border-ink-300"
            }`}
          >
            All ({levelRow.total})
          </button>
          {reportBreakdown.map((r) => (
            <button
              key={r.report}
              onClick={() => { setReportFilter(r.report); setPage(1); }}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                reportFilter === r.report ? `${soft} border` : "border-ink-200 bg-white text-ink-600 hover:border-ink-300"
              }`}
            >
              {r.report} ({r.count})
            </button>
          ))}
        </div>
      )}

      {/* Detail table */}
      <div className="mt-4 overflow-hidden rounded-lg border border-ink-200 bg-white">
        {isLoading ? (
          <LoadingBlock />
        ) : !data || data.rows.length === 0 ? (
          <EmptyState title="No sends in this window" subtitle="Try a wider time window above." />
        ) : (
          <>
            <Table>
              <thead>
                <tr><Th>Recipient</Th><Th>Report</Th><Th>Status</Th><Th>Date</Th><Th>Opened</Th></tr>
              </thead>
              <tbody>
                {data.rows.map((r) => (
                  <tr key={r.id}>
                    <Td>
                      <div className="font-medium text-ink-900">{r.recipientName}</div>
                      <div className="text-xs text-ink-400">{r.recipientEmail}</div>
                    </Td>
                    <Td className="text-ink-600">{r.report}</Td>
                    <Td>
                      <Badge tone={r.status === "Failed" ? "red" : r.isDraft ? "amber" : "green"}>
                        {r.status === "Failed" ? "Failed" : r.isDraft ? "Drafted" : "Sent"}
                      </Badge>
                    </Td>
                    <Td className="text-ink-500">{fmtDate(r.sentAt ?? r.createdAt)}</Td>
                    <Td>{r.opened ? <Badge tone="blue">Opened</Badge> : <span className="text-ink-300">—</span>}</Td>
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

export default function OutgoingLevelDashboard() {
  const [window, setWindowValue] = useState("30d");
  const [selectedLevel, setSelectedLevel] = useState<string | null>(null);

  const { data, isLoading } = useQuery<OutgoingByLevel>({
    queryKey: ["outgoing-by-level", window],
    queryFn: async () => (await api.get("/api/dashboard/outgoing-by-level", { params: { window } })).data,
  });

  if (isLoading || !data) return <Card><LoadingBlock /></Card>;

  const chartData = data.levels;
  const selected = selectedLevel ? data.levels.find((l) => l.level === selectedLevel) ?? null : null;
  const selectedReports = selectedLevel
    ? data.byLevelAndReport.filter((r) => r.level === selectedLevel)
    : [];

  return (
    <Card>
      <CardHeader
        title="Mail Volume by Level"
        subtitle="Every draft and send from this app's own report distribution — click a level to see who, what, and when."
        action={
          <div className="flex items-center gap-0.5 rounded-lg border border-ink-200 bg-ink-50 p-0.5">
            {WINDOWS.map((w) => (
              <button
                key={w.value}
                onClick={() => setWindowValue(w.value)}
                className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors ${
                  window === w.value ? "bg-white text-brand-700 shadow-sm" : "text-ink-500 hover:text-ink-800"
                }`}
              >
                {w.label}
              </button>
            ))}
          </div>
        }
      />

      {data.totalAcrossLevels === 0 ? (
        <EmptyState title="No outgoing mail in this window" subtitle="Widen the time window above, or draft a report from Scheduler." />
      ) : (
        <>
          <div className="px-5 pt-4">
            <ResponsiveContainer width="100%" height={Math.max(180, chartData.length * 44)}>
              <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 36, bottom: 4, left: 4 }} barCategoryGap={14}>
                <XAxis type="number" hide />
                <YAxis
                  type="category" dataKey="level" width={130} tickLine={false} axisLine={false}
                  tick={{ fill: "#4b443d", fontSize: 12, fontWeight: 500 }}
                />
                <Tooltip cursor={{ fill: "#e9e2d9", opacity: 0.4 }} content={<ChartTooltip />} />
                <Bar
                  dataKey="total" fill="#c1520a" radius={[0, 4, 4, 0]} maxBarSize={24} cursor="pointer"
                  isAnimationActive={false}
                  onClick={(d: unknown) => {
                    const lvl = (d as { level?: string })?.level;
                    if (lvl) setSelectedLevel((cur) => (cur === lvl ? null : lvl));
                  }}
                  label={{ position: "right", fill: "#7a6f64", fontSize: 12, fontWeight: 600 }}
                >
                  {chartData.map((row) => (
                    <Cell
                      key={row.level}
                      fill={LEVEL_COLOR[row.level] ?? "#a8998a"}
                      fillOpacity={selectedLevel && selectedLevel !== row.level ? 0.35 : 1}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="flex flex-wrap gap-3 px-5 pb-5 pt-2">
            {chartData.map((row) => (
              <LevelTile
                key={row.level}
                row={row}
                selected={selectedLevel === row.level}
                onClick={() => setSelectedLevel((cur) => (cur === row.level ? null : row.level))}
              />
            ))}
          </div>

          {selected && (
            <>
              <div className="flex items-center justify-between border-t border-ink-200 px-5 pt-3">
                <div className="flex items-center gap-2 py-2 text-sm font-semibold text-ink-800">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: LEVEL_COLOR[selected.level] ?? "#a8998a" }} />
                  {selected.level} — detail
                </div>
                <button
                  onClick={() => setSelectedLevel(null)}
                  className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-ink-500 hover:bg-ink-100"
                >
                  <X className="h-3.5 w-3.5" strokeWidth={2.25} />Close
                </button>
              </div>
              <DrillDown key={selected.level} levelRow={selected} reportBreakdown={selectedReports} window={window} />
            </>
          )}
        </>
      )}
    </Card>
  );
}
