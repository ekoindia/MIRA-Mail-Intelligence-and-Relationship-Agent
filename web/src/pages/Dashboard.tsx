import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  RefreshCw, TrendingUp, Users, UserX, Handshake, Clock, Send, PenLine, AlertTriangle, Mail, ArrowUpRight, Inbox,
} from "lucide-react";
import { api } from "../lib/api";
import { PageHeader, Card, CardHeader, Badge, LoadingBlock, EmptyState, Table, Th, Td, Toggle } from "../components/ui";
import IncomingSection, { useIncomingLive } from "./Incoming";
import OutgoingLevelDashboard from "../components/OutgoingLevelDashboard";

const REFRESH_MS = 45_000;

interface SchemeBlock { target: number; mtd: number; ftd: number; percent: number }
interface Business {
  pmjdy: SchemeBlock;
  schemes: { APY: SchemeBlock; PMSBY: SchemeBlock; PMJJBY: SchemeBlock };
  sssOverall: SchemeBlock;
  totalCsps: number;
  inactiveCsps: number;
  inactivePercent: number;
  loanLeadsMtd: number;
  activeLeadCsps: number;
}
interface LeaderRow { name: string; target: number; mtd: number; percent: number; cspCount: number }
interface Leaderboard { top: LeaderRow[]; bottom: LeaderRow[] }
interface AutomationStatus {
  autosendEnabled: boolean; fetchTime: string; sendTime: string;
  totalConfigured: number; activeCount: number; paused: string[]; merged: { report: string; mergedInto: string }[];
}
interface RecentJob { id: number; report: string; status: string; recipients: number; sent: number; failed: number; createdAt: string }
interface Operations {
  emailsSent: number; drafted: number; failed: number;
  openToday: { opened: number; total: number };
  recentJobs: RecentJob[];
}
interface DashboardData {
  lastSynced: string;
  business: Business;
  rboLeaderboard: Leaderboard;
  lhoLeaderboard: Leaderboard;
  automationStatus: AutomationStatus;
  operations: Operations;
}

function useRelativeTime(iso: string | undefined) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, []);
  if (!iso) return "";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  return `${Math.round(seconds / 60)}m ago`;
}

function toneForPercent(pct: number): "emerald" | "amber" | "rose" {
  if (pct >= 60) return "emerald";
  if (pct >= 30) return "amber";
  return "rose";
}

const BAR_COLORS: Record<string, string> = { emerald: "bg-emerald-500", amber: "bg-amber-500", rose: "bg-rose-500" };
const TEXT_COLORS: Record<string, string> = { emerald: "text-emerald-700", amber: "text-amber-700", rose: "text-rose-700" };

function ProgressBar({ percent }: { percent: number }) {
  const tone = toneForPercent(percent);
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-100">
      <div className={`h-1.5 rounded-full ${BAR_COLORS[tone]} transition-all`} style={{ width: `${Math.min(100, percent)}%` }} />
    </div>
  );
}

function BusinessKpiCard({
  label, percent, sub, icon: Icon,
}: { label: string; percent: number; sub: string; icon: React.ComponentType<{ className?: string; strokeWidth?: number }> }) {
  const tone = toneForPercent(percent);
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-500">{label}</span>
        <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${BAR_COLORS[tone].replace("bg-", "bg-").replace("500", "50")} ${TEXT_COLORS[tone]}`}>
          <Icon className="h-4 w-4" strokeWidth={2.25} />
        </div>
      </div>
      <div className="mt-3 flex items-baseline gap-1">
        <span className="font-mono text-3xl font-semibold tracking-tight tabular-nums text-ink-900">{percent}%</span>
      </div>
      <div className="mt-2"><ProgressBar percent={percent} /></div>
      <div className="mt-2 text-xs text-ink-500">{sub}</div>
    </Card>
  );
}

function CountKpiCard({
  label, value, sub, icon: Icon, tone,
}: { label: string; value: string | number; sub: string; icon: React.ComponentType<{ className?: string; strokeWidth?: number }>; tone: "brand" | "sky" | "rose" }) {
  const toneClasses: Record<string, string> = {
    brand: "bg-brand-50 text-brand-600", sky: "bg-sky-50 text-sky-600", rose: "bg-rose-50 text-rose-600",
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

function SchemeRow({ name, block }: { name: string; block: SchemeBlock }) {
  const tone = toneForPercent(block.percent);
  return (
    <tr>
      <Td className="font-medium text-ink-900">{name}</Td>
      <Td>{block.target.toLocaleString()}</Td>
      <Td>{block.mtd.toLocaleString()}</Td>
      <Td>{block.ftd.toLocaleString()}</Td>
      <Td>
        <div className="flex items-center gap-2">
          <div className="w-20"><ProgressBar percent={block.percent} /></div>
          <span className={`text-xs font-semibold ${TEXT_COLORS[tone]}`}>{block.percent}%</span>
        </div>
      </Td>
    </tr>
  );
}

function LeaderList({ title, rows, emptyLabel }: { title: string; rows: LeaderRow[]; emptyLabel: string }) {
  if (rows.length === 0) {
    return (
      <div>
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">{title}</div>
        <div className="rounded-lg border border-dashed border-ink-200 px-3 py-4 text-center text-xs text-ink-400">{emptyLabel}</div>
      </div>
    );
  }
  return (
    <div>
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-500">{title}</div>
      <div className="space-y-2.5">
        {rows.map((r, i) => {
          const tone = toneForPercent(r.percent);
          return (
            <div key={r.name} className="flex items-center gap-3">
              <span className="w-4 shrink-0 text-xs font-medium text-ink-400">{i + 1}</span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium text-ink-800" title={r.name}>{r.name}</span>
                  <span className={`shrink-0 text-xs font-semibold ${TEXT_COLORS[tone]}`}>{r.percent}%</span>
                </div>
                <div className="mt-1"><ProgressBar percent={r.percent} /></div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function LeaderboardCard({
  title, subtitle, data,
}: { title: string; subtitle: string; data: Leaderboard }) {
  return (
    <Card>
      <CardHeader title={title} subtitle={subtitle} />
      <div className="grid grid-cols-2 gap-6 px-5 py-5">
        <LeaderList title="Top Performers" rows={data.top} emptyLabel="No data yet" />
        <LeaderList title="Needs Attention" rows={data.bottom} emptyLabel="No data yet" />
      </div>
    </Card>
  );
}

function ViewToggle({
  view, onChange,
}: { view: "outgoing" | "incoming"; onChange: (v: "outgoing" | "incoming") => void }) {
  const options: { value: "outgoing" | "incoming"; label: string; icon: typeof ArrowUpRight }[] = [
    { value: "outgoing", label: "Outgoing", icon: ArrowUpRight },
    { value: "incoming", label: "Incoming", icon: Inbox },
  ];
  return (
    <div className="flex items-center gap-0.5 rounded-lg border border-ink-200 bg-ink-50 p-0.5">
      {options.map((o) => {
        const active = view === o.value;
        const Icon = o.icon;
        return (
          <button
            key={o.value}
            onClick={() => onChange(o.value)}
            className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              active ? "bg-white text-brand-700 shadow-sm" : "text-ink-500 hover:text-ink-800"
            }`}
          >
            <Icon className="h-3.5 w-3.5" strokeWidth={2.25} />
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export default function Dashboard() {
  const queryClient = useQueryClient();
  const [view, setView] = useState<"outgoing" | "incoming">("outgoing");
  const { data, isLoading, isFetching } = useQuery<DashboardData>({
    queryKey: ["dashboard"],
    queryFn: async () => (await api.get("/api/dashboard")).data,
    refetchInterval: REFRESH_MS,
    refetchOnWindowFocus: true,
  });
  const relativeSync = useRelativeTime(data?.lastSynced);
  const incomingLive = useIncomingLive();

  if (isLoading || !data) return <LoadingBlock />;

  const { business, rboLeaderboard, lhoLeaderboard, automationStatus, operations } = data;
  const openPct = operations.openToday.total > 0
    ? Math.round((operations.openToday.opened / operations.openToday.total) * 100) : 0;

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle={
          view === "outgoing"
            ? "This month's real progress across every RBO and LHO, from the live Calling Sheet."
            : "What's arriving in the connected inbox, and how much of it could be automated."
        }
        action={
          <div className="flex items-center gap-3">
            <ViewToggle view={view} onChange={setView} />
            {view === "outgoing" ? (
              <>
                <div className="flex items-center gap-1.5 text-xs text-ink-500">
                  <span className="relative flex h-2 w-2">
                    <span className={`absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 ${isFetching ? "animate-ping" : ""}`} />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
                  </span>
                  Live · synced {relativeSync}
                </div>
                <button
                  onClick={() => queryClient.invalidateQueries({ queryKey: ["dashboard"] })}
                  disabled={isFetching}
                  title="Refresh now"
                  className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 text-ink-500 hover:bg-ink-50 disabled:opacity-50"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`} strokeWidth={2.25} />
                </button>
              </>
            ) : (
              <>
                <div className="flex items-center gap-1.5 text-xs text-ink-500">
                  <span className="relative flex h-2 w-2">
                    <span
                      className={`absolute inline-flex h-full w-full rounded-full opacity-75 ${
                        incomingLive.syncStatus?.syncEnabled ? "bg-emerald-400" : "bg-ink-300"
                      } ${incomingLive.isRefreshing ? "animate-ping" : ""}`}
                    />
                    <span
                      className={`relative inline-flex h-2 w-2 rounded-full ${
                        incomingLive.syncStatus?.syncEnabled ? "bg-emerald-500" : "bg-ink-400"
                      }`}
                    />
                  </span>
                  {incomingLive.syncStatus?.syncEnabled ? `Live · refreshed ${incomingLive.relativeRefresh}` : "Live sync off"}
                  <Toggle
                    checked={!!incomingLive.syncStatus?.syncEnabled}
                    onChange={() => incomingLive.toggleAutoSync.mutate(!incomingLive.syncStatus?.syncEnabled)}
                    disabled={!incomingLive.syncStatus || incomingLive.toggleAutoSync.isPending}
                  />
                </div>
                <button
                  onClick={() => incomingLive.sync.mutate()}
                  disabled={incomingLive.sync.isPending}
                  title="Sync now"
                  className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 text-ink-500 hover:bg-ink-50 disabled:opacity-50"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${incomingLive.sync.isPending ? "animate-spin" : ""}`} strokeWidth={2.25} />
                </button>
              </>
            )}
          </div>
        }
      />

      {view === "incoming" ? (
        <IncomingSection live={incomingLive} />
      ) : (
      <>
      <div className="grid grid-cols-4 gap-4">
        <BusinessKpiCard
          label="PMJDY Achievement" percent={business.pmjdy.percent} icon={TrendingUp}
          sub={`${business.pmjdy.mtd.toLocaleString()} / ${business.pmjdy.target.toLocaleString()} MTD`}
        />
        <BusinessKpiCard
          label="SSS Achievement" percent={business.sssOverall.percent} icon={Handshake}
          sub={`${business.sssOverall.mtd.toLocaleString()} / ${business.sssOverall.target.toLocaleString()} MTD`}
        />
        <CountKpiCard
          label="Active CSPs" value={(business.totalCsps - business.inactiveCsps).toLocaleString()} icon={Users} tone="brand"
          sub={`${business.totalCsps.toLocaleString()} total in scope`}
        />
        <CountKpiCard
          label="Inactive CSPs" value={business.inactiveCsps.toLocaleString()} icon={UserX} tone="rose"
          sub={`${business.inactivePercent}% of all CSPs`}
        />
      </div>

      <div className="mt-6">
        <OutgoingLevelDashboard />
      </div>

      <div className="mt-6">
        <Card>
          <CardHeader title="Scheme-wise Progress" subtitle="Target vs. this month's achievement vs. today, summed across every CSP in scope" />
          <Table>
            <thead>
              <tr><Th>Scheme</Th><Th>Target</Th><Th>MTD</Th><Th>Today</Th><Th>Achievement</Th></tr>
            </thead>
            <tbody>
              <SchemeRow name="PMJDY (Account Opening)" block={business.pmjdy} />
              <SchemeRow name="APY" block={business.schemes.APY} />
              <SchemeRow name="PMSBY" block={business.schemes.PMSBY} />
              <SchemeRow name="PMJJBY" block={business.schemes.PMJJBY} />
            </tbody>
          </Table>
        </Card>
      </div>

      <div className="mt-6 grid grid-cols-2 gap-6">
        <LeaderboardCard
          title="RBO Leaderboard" subtitle="Ranked by PMJDY target achievement % this month"
          data={rboLeaderboard}
        />
        <LeaderboardCard
          title="LHO Leaderboard" subtitle="Ranked by PMJDY target achievement % this month"
          data={lhoLeaderboard}
        />
      </div>

      <div className="mt-6 grid grid-cols-3 gap-6">
        <Card className="col-span-1">
          <CardHeader
            title="Automation"
            subtitle="Daily fetch → draft cycle"
            action={<Badge tone={automationStatus.autosendEnabled ? "green" : "slate"}>{automationStatus.autosendEnabled ? "ON" : "OFF"}</Badge>}
          />
          <div className="space-y-3 px-5 py-4 text-sm text-ink-700">
            <div className="flex items-center gap-2">
              <Clock className="h-3.5 w-3.5 text-ink-400" strokeWidth={2} />
              Fetch {automationStatus.fetchTime}, draft at/after {automationStatus.sendTime}
            </div>
            <div>
              <span className="font-semibold text-ink-900">{automationStatus.activeCount}</span> of{" "}
              <span className="font-semibold text-ink-900">{automationStatus.totalConfigured}</span> reports active
            </div>
          </div>
        </Card>

        <Card className="col-span-2">
          <CardHeader title="Email Operations" subtitle="Since yesterday" />
          <div className="grid grid-cols-4 gap-4 px-5 py-4">
            <div>
              <div className="flex items-center gap-1.5 text-xs text-ink-500"><Send className="h-3.5 w-3.5" strokeWidth={2} />Sent</div>
              <div className="mt-1 text-xl font-semibold text-ink-900">{operations.emailsSent}</div>
            </div>
            <div>
              <div className="flex items-center gap-1.5 text-xs text-ink-500"><PenLine className="h-3.5 w-3.5" strokeWidth={2} />Drafted</div>
              <div className="mt-1 text-xl font-semibold text-ink-900">{operations.drafted}</div>
            </div>
            <div>
              <div className="flex items-center gap-1.5 text-xs text-ink-500"><AlertTriangle className="h-3.5 w-3.5" strokeWidth={2} />Failed</div>
              <div className="mt-1 text-xl font-semibold text-ink-900">{operations.failed}</div>
            </div>
            <div>
              <div className="flex items-center gap-1.5 text-xs text-ink-500"><Mail className="h-3.5 w-3.5" strokeWidth={2} />Opened Today</div>
              <div className="mt-1 text-xl font-semibold text-ink-900">{openPct}%</div>
              <div className="mt-0.5 text-xs text-ink-400">
                {operations.openToday.total === 0
                  ? "no recent sends"
                  : `${operations.openToday.opened} of ${operations.openToday.total} recent`}
              </div>
            </div>
          </div>
        </Card>
      </div>

      <div className="mt-6">
        <Card>
          <CardHeader title="Recent Distribution Jobs" subtitle="Since yesterday" />
          {operations.recentJobs.length === 0 ? (
            <EmptyState title="No distribution jobs yet" subtitle="Draft a report from Scheduler to get started." />
          ) : (
            <Table>
              <thead><tr><Th>Report</Th><Th>Status</Th><Th>Recipients</Th><Th>Sent</Th><Th>Failed</Th></tr></thead>
              <tbody>
                {operations.recentJobs.map((j) => (
                  <tr key={j.id}>
                    <Td>{j.report}</Td>
                    <Td><Badge tone={j.status === "Completed" ? "green" : j.status === "Failed" ? "red" : "amber"}>{j.status}</Badge></Td>
                    <Td>{j.recipients}</Td>
                    <Td>{j.sent}</Td>
                    <Td>{j.failed}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </div>
      </>
      )}
    </div>
  );
}
