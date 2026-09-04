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

// Same accent-per-metric palette as the email template's own cards (see
// email_templates.body_html for "Daily RBO Update") — PMJDY orange, APY
// blue, PMSBY green, PMJJBY amber.
const METRIC_THEME: Record<string, { line: string; fg: string; barBg: string }> = {
  PMJDY: { line: "#f6ad79", fg: "#c1520a", barBg: "#fce9d9" },
  APY: { line: "#c7d3f2", fg: "#2452c0", barBg: "#e4eafa" },
  PMSBY: { line: "#bfe0cb", fg: "#127a38", barBg: "#e2f2e7" },
  PMJJBY: { line: "#ecdcae", fg: "#8a6410", barBg: "#faf1dd" },
};

// Public, no-login page reached by clicking a metric card in an automated
// report email — see api/routers/report_detail.py. token identifies the
// EmailLog (and therefore the recipient), metric picks which scheme.
// Deliberately styled to match the email itself (gradient header, rounded
// card, same accent colors) rather than the app's internal dashboard UI —
// per explicit instruction, since this page IS the email's own detail
// view, opened outside the inbox.
export default function ReportDetail() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const metric = params.get("metric") ?? "";
  const theme = METRIC_THEME[metric] ?? METRIC_THEME.PMJDY;

  const { data, isLoading, error } = useQuery<DetailResponse>({
    queryKey: ["report-detail", token, metric],
    queryFn: async () => (await api.get(`/api/public/report-detail/${token}`, { params: { metric } })).data,
    enabled: !!token && !!metric,
    retry: false,
  });

  return (
    <div style={{ minHeight: "100vh", background: "#f4f1ea", padding: "32px 16px", fontFamily: "'Segoe UI',Helvetica,Arial,sans-serif" }}>
      <div style={{ maxWidth: 600, margin: "0 auto", background: "#ffffff", border: "1px solid #e9e2d9", borderRadius: 12, overflow: "hidden" }}>
        <div style={{ background: "linear-gradient(135deg,#f0751e 0%,#c1520a 55%,#8a3d09 100%)", padding: "26px 28px" }}>
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: "#ffe3c7", paddingBottom: 7 }}>
            Eko Bharat Ventures &middot; CSP-wise Breakdown
          </div>
          <div style={{ fontSize: 22, fontWeight: 800, color: "#ffffff", lineHeight: 1.3 }}>
            {data ? data.metric_label : "Loading..."}
          </div>
          {data && (
            <div style={{ fontSize: 13, color: "#ffe3c7", paddingTop: 4 }}>
              {data.recipient_type}: {data.recipient_name}
            </div>
          )}
        </div>

        {isLoading && (
          <div style={{ padding: "48px 26px", textAlign: "center", color: "#7a6f64", fontSize: 13 }}>Loading...</div>
        )}

        {error && (
          <div style={{ padding: "48px 26px", textAlign: "center", color: "#7a6f64", fontSize: 13 }}>
            {apiErrorMessage(error, "This link is no longer valid.")}
          </div>
        )}

        {data && (
          <>
            <div style={{ padding: "20px 20px 8px", display: "flex", gap: 12 }}>
              {[
                { label: "Target", value: data.target },
                { label: "MTD Achievement", value: data.mtd_achievement },
                { label: "FTD (as sent)", value: data.ftd_achievement },
              ].map((stat) => (
                <div
                  key={stat.label}
                  style={{ flex: 1, background: "#ffffff", border: `1px solid ${theme.line}`, borderRadius: 10, overflow: "hidden" }}
                >
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
                        <th
                          key={h}
                          style={{
                            borderBottom: `2px solid ${theme.fg}`, padding: "8px 10px", background: theme.barBg,
                            textAlign: "left", fontSize: 11, color: theme.fg, textTransform: "uppercase", letterSpacing: "0.04em",
                          }}
                        >
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
      </div>
    </div>
  );
}
