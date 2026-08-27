import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Reply, X, ChevronRight } from "lucide-react";
import { api } from "../lib/api";
import { Card, CardHeader, Badge, EmptyState, LoadingBlock, Table, Th, Td } from "./ui";

// task = needs action (most urgent) · info = read-only data · other =
// genuinely unclassified, still deserves a human look (NOT de-emphasized —
// see the tier's own definition) · noise = the one truly ignorable bucket,
// so it's the only tier given a muted/gray treatment. Validated 3-hue set
// (task/info/other) via dataviz skill's validate_palette.js — all checks
// pass; noise is intentionally desaturated per the "9th series folds into
// Other" guidance, generalised to the one low-priority tier here.
const TIER_COLOR: Record<string, string> = {
  task: "#c1520a",
  info: "#2452c0",
  other: "#b98811",
  noise: "#a8998a",
};
const TIER_SOFT: Record<string, string> = {
  task: "bg-brand-50 border-brand-200 text-brand-700",
  info: "bg-info-soft border-info-line text-info-fg",
  other: "bg-warn-soft border-warn-line text-warn-fg",
  noise: "bg-ink-100 border-ink-200 text-ink-500",
};
const TIER_META: Record<string, { label: string; blurb: string }> = {
  task: { label: "Needs action", blurb: "A real request someone is waiting on — a human still has to do the work." },
  info: { label: "Informational", blurb: "Data or status pushes. Worth reading, nothing to action." },
  other: { label: "Unclassified", blurb: "Genuine one-offs that don't fit a pattern — these deserve a human, not a bucket." },
  noise: { label: "No reply needed", blurb: "Marketing, calendar traffic, bounces, share requests." },
};
const TIER_ORDER = ["task", "info", "other", "noise"];

interface IntentRow { intent: string; count: number }
interface TierRow { tier: string; count: number; pct: number; intents: IntentRow[] }
interface TriageSummary { total: number; by_tier: TierRow[] }
interface MessageRow {
  id: number; receivedAt: string | null; sender: string; subject: string;
  tier: string; intent: string | null; replied: boolean;
}
interface MessagesResponse { total: number; page: number; pageSize: number; rows: MessageRow[] }

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true });
}

function ChartTooltip({ active, payload }: { active?: boolean; payload?: { payload: TierRow }[] }) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0].payload;
  const meta = TIER_META[row.tier];
  return (
    <div className="rounded-lg border border-ink-200 bg-white px-3 py-2 text-xs shadow-lg">
      <div className="mb-1 flex items-center gap-1.5 font-semibold text-ink-900">
        <span className="h-2 w-2 rounded-full" style={{ background: TIER_COLOR[row.tier] ?? "#a8998a" }} />
        {meta?.label ?? row.tier}
      </div>
      <div className="text-ink-600">{row.count.toLocaleString()} email{row.count === 1 ? "" : "s"} ({Math.round(row.pct * 100)}%)</div>
    </div>
  );
}

function TierTile({
  row, selected, onClick,
}: { row: TierRow; selected: boolean; onClick: () => void }) {
  const soft = TIER_SOFT[row.tier] ?? "border-ink-200 bg-white";
  const meta = TIER_META[row.tier];
  return (
    <button
      onClick={onClick}
      className={`flex-1 rounded-xl border px-4 py-3.5 text-left transition-all ${
        selected ? `${soft} ring-2 ring-offset-1` : "border-ink-200 bg-white hover:border-ink-300 hover:shadow-sm"
      }`}
      style={selected ? { boxShadow: `0 0 0 2px ${TIER_COLOR[row.tier] ?? "#a8998a"}22` } : undefined}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-ink-500">
          <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: TIER_COLOR[row.tier] ?? "#a8998a" }} />
          {meta?.label ?? row.tier}
        </span>
        <ChevronRight className={`h-3.5 w-3.5 shrink-0 text-ink-300 transition-transform ${selected ? "rotate-90" : ""}`} strokeWidth={2.25} />
      </div>
      <div className="mt-2 font-mono text-2xl font-semibold tabular-nums text-ink-900">{row.count.toLocaleString()}</div>
      <div className="mt-1 text-xs text-ink-500">{Math.round(row.pct * 100)}% of SBI mail</div>
    </button>
  );
}

function DrillDown({ tierRow }: { tierRow: TierRow }) {
  const [intentFilter, setIntentFilter] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 8;

  const { data, isLoading } = useQuery<MessagesResponse>({
    queryKey: ["incoming-messages-paged-sbi", tierRow.tier, intentFilter, page],
    queryFn: async () => (
      await api.get("/api/incoming/messages", {
        params: { tier: tierRow.tier, intent: intentFilter ?? undefined, page, pageSize, sbi_only: true },
      })
    ).data,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;
  const soft = TIER_SOFT[tierRow.tier] ?? "border-ink-200 bg-white";

  return (
    <div className="border-t border-ink-200 bg-ink-50/60 px-5 py-5">
      <p className="text-xs text-ink-500">{TIER_META[tierRow.tier]?.blurb}</p>

      {tierRow.intents.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="text-xs font-medium text-ink-500">By type:</span>
          <button
            onClick={() => { setIntentFilter(null); setPage(1); }}
            className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
              intentFilter === null ? "border-ink-800 bg-ink-800 text-white" : "border-ink-200 bg-white text-ink-600 hover:border-ink-300"
            }`}
          >
            All ({tierRow.count})
          </button>
          {tierRow.intents.map((i) => (
            <button
              key={i.intent}
              onClick={() => { setIntentFilter(i.intent); setPage(1); }}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                intentFilter === i.intent ? `${soft} border` : "border-ink-200 bg-white text-ink-600 hover:border-ink-300"
              }`}
            >
              {i.intent} ({i.count})
            </button>
          ))}
        </div>
      )}

      <div className="mt-4 overflow-hidden rounded-lg border border-ink-200 bg-white">
        {isLoading ? (
          <LoadingBlock />
        ) : !data || data.rows.length === 0 ? (
          <EmptyState title="Nothing in this bucket" subtitle="Try a different type above, or run a sync to pull in more mail." />
        ) : (
          <>
            <Table>
              <thead>
                <tr><Th>Received</Th><Th>From</Th><Th>Subject</Th><Th>Type</Th><Th>Replied</Th></tr>
              </thead>
              <tbody>
                {data.rows.map((m) => (
                  <tr key={m.id}>
                    <Td className="whitespace-nowrap text-ink-500">{fmtDate(m.receivedAt)}</Td>
                    <Td className="max-w-[160px] truncate text-ink-600">{m.sender}</Td>
                    <Td>
                      <div className="max-w-md truncate font-medium text-ink-900" title={m.subject}>{m.subject}</div>
                    </Td>
                    <Td className="whitespace-nowrap text-ink-500">{m.intent ?? "—"}</Td>
                    <Td>{m.replied ? <Badge tone="green">Replied</Badge> : <span className="text-ink-300">—</span>}</Td>
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

export default function IncomingMailBreakdown() {
  const [selectedTier, setSelectedTier] = useState<string | null>(null);

  // SBI-scoped (sbi_only=true) — this app automates SBI-domain incoming
  // mail specifically, so the breakdown should reflect that mail, not
  // every direct email regardless of sender. Distinct query key from
  // Incoming.tsx's own "incoming-triage-summary" fetch (used for the
  // page-level "No Reply Needed" KPI, which stays all-sender) so the two
  // don't collide in the cache.
  const { data, isLoading } = useQuery<TriageSummary>({
    queryKey: ["incoming-triage-summary-sbi"],
    queryFn: async () => (await api.get("/api/incoming/triage-summary", { params: { sbi_only: true } })).data,
    refetchInterval: 45_000,
  });

  if (isLoading || !data) return <Card><LoadingBlock /></Card>;

  const chartData = [...data.by_tier].sort(
    (a, b) => TIER_ORDER.indexOf(a.tier) - TIER_ORDER.indexOf(b.tier),
  );
  const selected = selectedTier ? data.by_tier.find((t) => t.tier === selectedTier) ?? null : null;

  return (
    <Card>
      <CardHeader
        title="SBI Mail Breakdown"
        subtitle="Direct mail from SBI domains only, tagged by what it needs — plain keyword rules, no AI model. Click a type to see the actual messages. Tagging only: nothing is filed, replied to, or changed in Gmail."
        action={
          <div className="flex items-center gap-1.5 text-xs text-ink-500">
            <Reply className="h-3.5 w-3.5" strokeWidth={2.25} />
            {data.total.toLocaleString()} SBI emails
          </div>
        }
      />

      {data.total === 0 ? (
        <EmptyState title="No mail synced yet" subtitle="Click Sync now above to pull in incoming mail." />
      ) : (
        <>
          <div className="px-5 pt-4">
            <ResponsiveContainer width="100%" height={Math.max(140, chartData.length * 48)}>
              <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 44, bottom: 4, left: 4 }} barCategoryGap={16}>
                <XAxis type="number" hide />
                <YAxis
                  type="category"
                  dataKey={(row: TierRow) => TIER_META[row.tier]?.label ?? row.tier}
                  width={130} tickLine={false} axisLine={false}
                  tick={{ fill: "#4b443d", fontSize: 12, fontWeight: 500 }}
                />
                <Tooltip cursor={{ fill: "#e9e2d9", opacity: 0.4 }} content={<ChartTooltip />} />
                <Bar
                  dataKey="count" fill="#c1520a" radius={[0, 4, 4, 0]} maxBarSize={24} cursor="pointer"
                  isAnimationActive={false}
                  onClick={(d: unknown) => {
                    const tier = (d as { tier?: string })?.tier;
                    if (tier) setSelectedTier((cur) => (cur === tier ? null : tier));
                  }}
                  label={{ position: "right", fill: "#7a6f64", fontSize: 12, fontWeight: 600 }}
                >
                  {chartData.map((row) => (
                    <Cell
                      key={row.tier}
                      fill={TIER_COLOR[row.tier] ?? "#a8998a"}
                      fillOpacity={selectedTier && selectedTier !== row.tier ? 0.35 : 1}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="flex flex-wrap gap-3 px-5 pb-5 pt-2">
            {chartData.map((row) => (
              <TierTile
                key={row.tier}
                row={row}
                selected={selectedTier === row.tier}
                onClick={() => setSelectedTier((cur) => (cur === row.tier ? null : row.tier))}
              />
            ))}
          </div>

          {selected && (
            <>
              <div className="flex items-center justify-between border-t border-ink-200 px-5 pt-3">
                <div className="flex items-center gap-2 py-2 text-sm font-semibold text-ink-800">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: TIER_COLOR[selected.tier] ?? "#a8998a" }} />
                  {TIER_META[selected.tier]?.label ?? selected.tier} — detail
                </div>
                <button
                  onClick={() => setSelectedTier(null)}
                  className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-ink-500 hover:bg-ink-100"
                >
                  <X className="h-3.5 w-3.5" strokeWidth={2.25} />Close
                </button>
              </div>
              <DrillDown key={selected.tier} tierRow={selected} />
            </>
          )}
        </>
      )}
    </Card>
  );
}
