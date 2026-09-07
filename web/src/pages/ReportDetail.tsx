import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, apiErrorMessage } from "../lib/api";

interface DetailRow {
  csp_code: string;
  csp_name: string;
  branch_name: string;
  mtd: number;
  ftd: number;
}

interface DetailResponse {
  metric: string;
  metric_label: string;
  recipient_type: string;
  recipient_name: string;
  target: number;
  mtd_achievement: number;
  ftd_achievement: number;
  csp_count: number;
  csps_with_activity: number;
  rows: DetailRow[];
}

interface GrowthRow {
  csp_code: string;
  csp_name: string;
  branch_name: string;
  current_mtd: number;
  previous_mtd: number;
  delta: number;
}

interface GrowthResponse {
  metric: string;
  recipient_type: string;
  recipient_name: string;
  has_prior_week: boolean;
  prior_week_date: string | null;
  total_current: number;
  total_previous: number;
  rows: GrowthRow[];
}

// Same accent-per-metric palette as the email template's own cards (see
// email_templates.body_html for "Daily RBO Update" / "Weekly RBO Update")
// — PMJDY orange, APY blue, PMSBY green, PMJJBY amber.
const METRIC_THEME: Record<string, { line: string; fg: string; barBg: string }> = {
  PMJDY: { line: "#f6ad79", fg: "#c1520a", barBg: "#fce9d9" },
  APY: { line: "#c7d3f2", fg: "#2452c0", barBg: "#e4eafa" },
  PMSBY: { line: "#bfe0cb", fg: "#127a38", barBg: "#e2f2e7" },
  PMJJBY: { line: "#ecdcae", fg: "#8a6410", barBg: "#faf1dd" },
};

const METRIC_LABEL: Record<string, string> = {
  PMJDY: "PMJDY (Account Opening)",
  APY: "Atal Pension Yojana",
  PMSBY: "PM Suraksha Bima Yojana",
  PMJJBY: "PM Jeevan Jyoti Bima Yojana",
  LL: "Loan Lead Generation",
};

const INCOME_THEME = { line: "#f6ad79", fg: "#c1520a", barBg: "#fce9d9" };

function Shell({
  theme, eyebrow, title, subtitle, isLoading, error, children,
}: {
  theme: { line: string; fg: string; barBg: string }; eyebrow: string; title: string; subtitle?: string;
  isLoading: boolean; error: unknown; children?: React.ReactNode;
}) {
  return (
    <div style={{ minHeight: "100vh", background: "#f4f1ea", padding: "32px 16px", fontFamily: "'Segoe UI',Helvetica,Arial,sans-serif" }}>
      <div style={{ maxWidth: 600, margin: "0 auto", background: "#ffffff", border: "1px solid #e9e2d9", borderRadius: 12, overflow: "hidden" }}>
        <div style={{ background: "linear-gradient(135deg,#f0751e 0%,#c1520a 55%,#8a3d09 100%)", padding: "26px 28px" }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: "#ffe3c7", paddingBottom: 7 }}>
            {eyebrow}
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#ffffff", lineHeight: 1.3 }}>{title}</div>
          {subtitle && <div style={{ fontSize: 13, color: "#ffe3c7", paddingTop: 4 }}>{subtitle}</div>}
        </div>

        {isLoading && (
          <div style={{ padding: "48px 26px", textAlign: "center", color: "#7a6f64", fontSize: 13 }}>Loading...</div>
        )}
        {error !== undefined && error !== null && (
          <div style={{ padding: "48px 26px", textAlign: "center", color: "#7a6f64", fontSize: 13 }}>
            {apiErrorMessage(error, "This link is no longer valid.")}
          </div>
        )}
        {!isLoading && !error && children}
      </div>
    </div>
  );
}

function CurrentDetail({ token, metric }: { token: string; metric: string }) {
  const theme = METRIC_THEME[metric] ?? METRIC_THEME.PMJDY;
  const { data, isLoading, error } = useQuery<DetailResponse>({
    queryKey: ["report-detail", token, metric],
    queryFn: async () => (await api.get(`/api/public/report-detail/${token}`, { params: { metric } })).data,
    enabled: !!token && !!metric,
    retry: false,
  });

  return (
    <Shell
      theme={theme} eyebrow="Eko Bharat Ventures · CSP-wise Breakdown"
      title={data ? data.metric_label : METRIC_LABEL[metric] ?? "Loading..."}
      subtitle={data ? `${data.recipient_type}: ${data.recipient_name}` : undefined}
      isLoading={isLoading} error={error}
    >
      {data && (
        <>
          <div style={{ padding: "20px 20px 8px", display: "flex", gap: 12 }}>
            {[
              { label: "Target", value: data.target },
              { label: "MTD Achievement", value: data.mtd_achievement },
              { label: "FTD (as sent)", value: data.ftd_achievement },
            ].map((stat) => (
              <div key={stat.label} style={{ flex: 1, background: "#ffffff", border: `1px solid ${theme.line}`, borderRadius: 10, overflow: "hidden" }}>
                <div style={{ height: 4, background: theme.fg }} />
                <div style={{ padding: "10px 12px 2px", fontSize: 10, fontWeight: 800, letterSpacing: "0.05em", textTransform: "uppercase", color: theme.fg }}>
                  {stat.label}
                </div>
                <div style={{ padding: "0 12px 12px", fontSize: 26, fontWeight: 800, color: theme.fg, fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>
                  {stat.value}
                </div>
              </div>
            ))}
          </div>

          <div style={{ padding: "8px 20px 4px", fontSize: 11, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: theme.fg }}>
            CSP-wise Detail
          </div>

          <div style={{ padding: "0 20px 20px", overflowX: "auto" }}>
            {data.rows.length === 0 ? (
              <div style={{ padding: "24px 0", textAlign: "center", color: "#7a6f64", fontSize: 13 }}>
                No CSP has any achievement yet for this scheme.
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#312b26" }}>
                <thead>
                  <tr>
                    {["CSP Code", "CSP Name", "Branch", "MTD", "FTD"].map((h) => (
                      <th key={h} style={{ borderBottom: `2px solid ${theme.fg}`, padding: "8px 10px", background: theme.barBg, textAlign: "left", fontSize: 11, color: theme.fg, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.csp_code}>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.csp_code}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.csp_name}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.branch_name}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px", fontVariantNumeric: "tabular-nums" }}>{r.mtd}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px", fontVariantNumeric: "tabular-nums" }}>{r.ftd}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div style={{ marginTop: 12, textAlign: "center", fontSize: 11, color: "#7a6f64" }}>
              {data.csps_with_activity} of {data.csp_count} CSP(s) in scope have activity this month.
            </div>
          </div>
        </>
      )}
    </Shell>
  );
}

function GrowthDetail({ token, metric }: { token: string; metric: string }) {
  const theme = METRIC_THEME[metric] ?? METRIC_THEME.PMJDY;
  const { data, isLoading, error } = useQuery<GrowthResponse>({
    queryKey: ["report-growth", token, metric],
    queryFn: async () => (await api.get(`/api/public/report-detail/${token}/growth`, { params: { metric } })).data,
    enabled: !!token && !!metric,
    retry: false,
  });

  const netDelta = data ? data.total_current - data.total_previous : 0;

  return (
    <Shell
      theme={theme} eyebrow="Eko Bharat Ventures · CSP-wise Growth"
      title={data ? `${METRIC_LABEL[data.metric] ?? data.metric} — Week-over-Week` : METRIC_LABEL[metric] ?? "Loading..."}
      subtitle={data ? `${data.recipient_type}: ${data.recipient_name}` : undefined}
      isLoading={isLoading} error={error}
    >
      {data && !data.has_prior_week && (
        <div style={{ padding: "24px 20px", textAlign: "center", color: "#7a6f64", fontSize: 13 }}>
          No prior week's data on record yet for this recipient — growth comparison will appear once next week's report has a baseline to compare against.
        </div>
      )}

      {data && data.has_prior_week && (
        <>
          <div style={{ padding: "20px 20px 8px", display: "flex", gap: 12 }}>
            <div style={{ flex: 1, background: "#ffffff", border: `1px solid ${theme.line}`, borderRadius: 10, overflow: "hidden" }}>
              <div style={{ height: 4, background: theme.fg }} />
              <div style={{ padding: "10px 12px 2px", fontSize: 10, fontWeight: 800, letterSpacing: "0.05em", textTransform: "uppercase", color: theme.fg }}>Previous Week</div>
              <div style={{ padding: "0 12px 12px", fontSize: 26, fontWeight: 800, color: theme.fg, fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>{data.total_previous}</div>
            </div>
            <div style={{ flex: 1, background: "#ffffff", border: `1px solid ${theme.line}`, borderRadius: 10, overflow: "hidden" }}>
              <div style={{ height: 4, background: theme.fg }} />
              <div style={{ padding: "10px 12px 2px", fontSize: 10, fontWeight: 800, letterSpacing: "0.05em", textTransform: "uppercase", color: theme.fg }}>This Week</div>
              <div style={{ padding: "0 12px 12px", fontSize: 26, fontWeight: 800, color: theme.fg, fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>{data.total_current}</div>
            </div>
            <div style={{ flex: 1, background: "#ffffff", border: `1px solid ${theme.line}`, borderRadius: 10, overflow: "hidden" }}>
              <div style={{ height: 4, background: netDelta >= 0 ? "#127a38" : "#b3261e" }} />
              <div style={{ padding: "10px 12px 2px", fontSize: 10, fontWeight: 800, letterSpacing: "0.05em", textTransform: "uppercase", color: netDelta >= 0 ? "#127a38" : "#b3261e" }}>Net Growth</div>
              <div style={{ padding: "0 12px 12px", fontSize: 26, fontWeight: 800, color: netDelta >= 0 ? "#127a38" : "#b3261e", fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>
                {netDelta >= 0 ? "+" : ""}{netDelta}
              </div>
            </div>
          </div>

          <div style={{ padding: "8px 20px 4px", fontSize: 11, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: theme.fg }}>
            CSP-wise Growth {data.prior_week_date && <span style={{ fontWeight: 400, textTransform: "none", color: "#7a6f64" }}>&nbsp;vs week of {data.prior_week_date}</span>}
          </div>

          <div style={{ padding: "0 20px 20px", overflowX: "auto" }}>
            {data.rows.length === 0 ? (
              <div style={{ padding: "24px 0", textAlign: "center", color: "#7a6f64", fontSize: 13 }}>No CSP activity to compare.</div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#312b26" }}>
                <thead>
                  <tr>
                    {["CSP Code", "CSP Name", "Branch", "Prev Wk", "This Wk", "Growth"].map((h) => (
                      <th key={h} style={{ borderBottom: `2px solid ${theme.fg}`, padding: "8px 10px", background: theme.barBg, textAlign: "left", fontSize: 11, color: theme.fg, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.csp_code}>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.csp_code}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.csp_name}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.branch_name}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px", fontVariantNumeric: "tabular-nums" }}>{r.previous_mtd}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px", fontVariantNumeric: "tabular-nums" }}>{r.current_mtd}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px", fontVariantNumeric: "tabular-nums", fontWeight: 700, color: r.delta > 0 ? "#127a38" : r.delta < 0 ? "#b3261e" : "#7a6f64" }}>
                        {r.delta > 0 ? "+" : ""}{r.delta}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </Shell>
  );
}

interface IncomeRow {
  csp_code: string;
  csp_name: string;
  branch_name: string;
  curr_month: number;
  prev_month: number;
  delta: number;
}

interface IncomeResponse {
  recipient_type: string;
  recipient_name: string;
  csp_count: number;
  total_curr: number;
  total_prev: number;
  rows: IncomeRow[];
}

const fmtRupee = (n: number) =>
  `₹${n.toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

function IncomeDetail({ token }: { token: string }) {
  const theme = INCOME_THEME;
  const { data, isLoading, error } = useQuery<IncomeResponse>({
    queryKey: ["report-income", token],
    queryFn: async () => (await api.get(`/api/public/report-detail/${token}/income`)).data,
    enabled: !!token,
    retry: false,
  });

  const netDelta = data ? data.total_curr - data.total_prev : 0;

  return (
    <Shell
      theme={theme} eyebrow="Eko Bharat Ventures · CSP Income Detail"
      title="CSP Income — Month-over-Month"
      subtitle={data ? `${data.recipient_type}: ${data.recipient_name}` : undefined}
      isLoading={isLoading} error={error}
    >
      {data && (
        <>
          <div style={{ padding: "20px 20px 8px", display: "flex", gap: 12 }}>
            <div style={{ flex: 1, background: "#ffffff", border: `1px solid ${theme.line}`, borderRadius: 10, overflow: "hidden" }}>
              <div style={{ height: 4, background: theme.fg }} />
              <div style={{ padding: "10px 12px 2px", fontSize: 10, fontWeight: 800, letterSpacing: "0.05em", textTransform: "uppercase", color: theme.fg }}>Previous Month</div>
              <div style={{ padding: "0 12px 12px", fontSize: 22, fontWeight: 800, color: theme.fg, fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>{fmtRupee(data.total_prev)}</div>
            </div>
            <div style={{ flex: 1, background: "#ffffff", border: `1px solid ${theme.line}`, borderRadius: 10, overflow: "hidden" }}>
              <div style={{ height: 4, background: theme.fg }} />
              <div style={{ padding: "10px 12px 2px", fontSize: 10, fontWeight: 800, letterSpacing: "0.05em", textTransform: "uppercase", color: theme.fg }}>This Month</div>
              <div style={{ padding: "0 12px 12px", fontSize: 22, fontWeight: 800, color: theme.fg, fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>{fmtRupee(data.total_curr)}</div>
            </div>
            <div style={{ flex: 1, background: "#ffffff", border: `1px solid ${theme.line}`, borderRadius: 10, overflow: "hidden" }}>
              <div style={{ height: 4, background: netDelta >= 0 ? "#127a38" : "#b3261e" }} />
              <div style={{ padding: "10px 12px 2px", fontSize: 10, fontWeight: 800, letterSpacing: "0.05em", textTransform: "uppercase", color: netDelta >= 0 ? "#127a38" : "#b3261e" }}>Net Change</div>
              <div style={{ padding: "0 12px 12px", fontSize: 22, fontWeight: 800, color: netDelta >= 0 ? "#127a38" : "#b3261e", fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>
                {netDelta >= 0 ? "+" : ""}{fmtRupee(netDelta)}
              </div>
            </div>
          </div>

          <div style={{ padding: "8px 20px 4px", fontSize: 11, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: theme.fg }}>
            CSP-wise Income
          </div>

          <div style={{ padding: "0 20px 20px", overflowX: "auto" }}>
            {data.rows.length === 0 ? (
              <div style={{ padding: "24px 0", textAlign: "center", color: "#7a6f64", fontSize: 13 }}>No CSP income recorded.</div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#312b26" }}>
                <thead>
                  <tr>
                    {["CSP Code", "CSP Name", "Branch", "Prev Month", "This Month", "Change"].map((h) => (
                      <th key={h} style={{ borderBottom: `2px solid ${theme.fg}`, padding: "8px 10px", background: theme.barBg, textAlign: "left", fontSize: 11, color: theme.fg, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.csp_code}>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.csp_code}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.csp_name}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.branch_name}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px", fontVariantNumeric: "tabular-nums" }}>{fmtRupee(r.prev_month)}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px", fontVariantNumeric: "tabular-nums" }}>{fmtRupee(r.curr_month)}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px", fontVariantNumeric: "tabular-nums", fontWeight: 700, color: r.delta > 0 ? "#127a38" : r.delta < 0 ? "#b3261e" : "#7a6f64" }}>
                        {r.delta > 0 ? "+" : ""}{fmtRupee(r.delta)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </Shell>
  );
}

interface InactiveRow {
  csp_code: string;
  csp_name: string;
  branch_name: string;
  circle: string;
  inactivity_days: number | null;
}

interface InactiveResponse {
  recipient_type: string;
  recipient_name: string;
  total_csp_count: number;
  inactive_count: number;
  circle_distribution: { circle: string; count: number }[];
  rows: InactiveRow[];
}

const INACTIVE_THEME = { line: "#ecdcae", fg: "#8a6410", barBg: "#faf1dd" };

function InactiveDetail({ token }: { token: string }) {
  const theme = INACTIVE_THEME;
  const { data, isLoading, error } = useQuery<InactiveResponse>({
    queryKey: ["report-inactive", token],
    queryFn: async () => (await api.get(`/api/public/report-detail/${token}/inactive`)).data,
    enabled: !!token,
    retry: false,
  });

  const maxCircleCount = data ? Math.max(...data.circle_distribution.map((c) => c.count), 1) : 1;

  return (
    <Shell
      theme={theme} eyebrow="Eko Bharat Ventures · Inactive CSP Detail"
      title="Inactive CSPs — Circle Spread"
      subtitle={data ? `${data.recipient_type}: ${data.recipient_name}` : undefined}
      isLoading={isLoading} error={error}
    >
      {data && (
        <>
          <div style={{ padding: "20px 20px 8px", display: "flex", gap: 12 }}>
            <div style={{ flex: 1, background: "#ffffff", border: `1px solid ${theme.line}`, borderRadius: 10, overflow: "hidden" }}>
              <div style={{ height: 4, background: theme.fg }} />
              <div style={{ padding: "10px 12px 2px", fontSize: 10, fontWeight: 800, letterSpacing: "0.05em", textTransform: "uppercase", color: theme.fg }}>Inactive CSPs</div>
              <div style={{ padding: "0 12px 12px", fontSize: 26, fontWeight: 800, color: theme.fg, fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>{data.inactive_count}</div>
            </div>
            <div style={{ flex: 1, background: "#ffffff", border: `1px solid ${theme.line}`, borderRadius: 10, overflow: "hidden" }}>
              <div style={{ height: 4, background: theme.fg }} />
              <div style={{ padding: "10px 12px 2px", fontSize: 10, fontWeight: 800, letterSpacing: "0.05em", textTransform: "uppercase", color: theme.fg }}>Total CSPs</div>
              <div style={{ padding: "0 12px 12px", fontSize: 26, fontWeight: 800, color: theme.fg, fontVariantNumeric: "tabular-nums", lineHeight: 1.1 }}>{data.total_csp_count}</div>
            </div>
          </div>

          <div style={{ padding: "8px 20px 4px", fontSize: 11, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: theme.fg }}>
            Circle Spread
          </div>
          <div style={{ padding: "4px 20px 16px" }}>
            {data.circle_distribution.map((c) => (
              <div key={c.circle} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <div style={{ width: 90, fontSize: 12, color: "#312b26" }}>{c.circle}</div>
                <div style={{ flex: 1, background: theme.barBg, borderRadius: 99, height: 10, overflow: "hidden" }}>
                  <div style={{ width: `${(c.count / maxCircleCount) * 100}%`, background: theme.fg, height: 10, borderRadius: 99 }} />
                </div>
                <div style={{ width: 24, textAlign: "right", fontSize: 12, fontWeight: 700, color: theme.fg }}>{c.count}</div>
              </div>
            ))}
          </div>

          <div style={{ padding: "8px 20px 4px", fontSize: 11, fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: theme.fg }}>
            CSP-wise Detail
          </div>
          <div style={{ padding: "0 20px 20px", overflowX: "auto" }}>
            {data.rows.length === 0 ? (
              <div style={{ padding: "24px 0", textAlign: "center", color: "#7a6f64", fontSize: 13 }}>No inactive CSPs.</div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, color: "#312b26" }}>
                <thead>
                  <tr>
                    {["CSP Code", "CSP Name", "Branch", "Circle", "Inactive (days)"].map((h) => (
                      <th key={h} style={{ borderBottom: `2px solid ${theme.fg}`, padding: "8px 10px", background: theme.barBg, textAlign: "left", fontSize: 11, color: theme.fg, textTransform: "uppercase", letterSpacing: "0.04em" }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r) => (
                    <tr key={r.csp_code}>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.csp_code}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.csp_name}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.branch_name}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px" }}>{r.circle}</td>
                      <td style={{ border: "1px solid #e9e2d9", padding: "6px 10px", fontVariantNumeric: "tabular-nums" }}>{r.inactivity_days ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </Shell>
  );
}

// Public, no-login page reached by clicking a metric card in an automated
// report email — see api/routers/report_detail.py (daily "current
// breakdown") and report_growth_service.py (weekly "week-over-week"
// growth, mode=growth). token identifies the EmailLog (and therefore the
// recipient), metric picks which scheme. Deliberately styled to match the
// email itself (gradient header, rounded card, same accent colors) rather
// than the app's internal dashboard UI — this page IS the email's own
// detail view, opened outside the inbox.
export default function ReportDetail() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const metric = params.get("metric") ?? "";
  const mode = params.get("mode") ?? "current";

  if (mode === "growth") return <GrowthDetail token={token} metric={metric} />;
  if (mode === "income") return <IncomeDetail token={token} />;
  if (mode === "inactive") return <InactiveDetail token={token} />;
  return <CurrentDetail token={token} metric={metric} />;
}
