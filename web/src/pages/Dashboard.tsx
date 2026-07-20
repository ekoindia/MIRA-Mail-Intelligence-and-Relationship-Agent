import { useQuery } from "@tanstack/react-query";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from "recharts";
import { FileText, Send, Inbox as InboxIcon, AlertTriangle } from "lucide-react";
import { api } from "../lib/api";
import { PageHeader, Card, CardHeader, KpiCard, LoadingBlock, EmptyState, Table, Th, Td, Badge } from "../components/ui";

interface DashboardData {
  kpis: { reportsUploaded: number; totalOutgoing: number; totalIncoming: number; failedEmails: number };
  mailByLho: { lho: string; incoming: number; outgoing: number }[];
  recentIncoming: { Timestamp: string; "Report Type": string; LHO: string; Status: string }[];
  recentOutgoing: { Timestamp: string; Recipient: string; Email: string; LHO: string; Status: string; Via: string }[];
  recentJobs: { id: number; report: string; status: string; recipients: number; sent: number; failed: number; createdAt: string }[];
}

export default function Dashboard() {
  const { data, isLoading } = useQuery<DashboardData>({
    queryKey: ["dashboard"],
    queryFn: async () => (await api.get("/api/dashboard")).data,
  });

  if (isLoading || !data) return <LoadingBlock />;

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="Report distribution activity and mail traffic overview." />

      <div className="grid grid-cols-4 gap-4">
        <KpiCard label="Reports Uploaded" value={data.kpis.reportsUploaded} icon={FileText} tone="brand" />
        <KpiCard label="Total Outgoing" value={data.kpis.totalOutgoing} icon={Send} tone="emerald" />
        <KpiCard label="Total Incoming" value={data.kpis.totalIncoming} icon={InboxIcon} tone="sky" />
        <KpiCard label="Failed Emails" value={data.kpis.failedEmails} icon={AlertTriangle} tone="rose" />
      </div>

      <div className="mt-6">
        <Card>
          <CardHeader title="Mail Activity by LHO" subtitle="Incoming vs. outgoing volume per LHO" />
          <div className="px-5 py-5">
            {data.mailByLho.length === 0 ? (
              <EmptyState title="No LHO-tagged mail activity yet" />
            ) : (
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={data.mailByLho}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="lho" tick={{ fontSize: 12, fill: "#64748b" }} axisLine={{ stroke: "#e2e8f0" }} tickLine={false} />
                  <YAxis tick={{ fontSize: 12, fill: "#64748b" }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip contentStyle={{ borderRadius: 8, border: "1px solid #e2e8f0", fontSize: 12 }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="incoming" name="Incoming" fill="#0ea5e9" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="outgoing" name="Outgoing" fill="#10b981" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
      </div>

      <div className="mt-6 grid grid-cols-2 gap-6">
        <Card>
          <CardHeader title="Recent Incoming" />
          {data.recentIncoming.length === 0 ? (
            <EmptyState title="No incoming mail yet" />
          ) : (
            <Table>
              <thead><tr><Th>Report</Th><Th>LHO</Th><Th>Status</Th></tr></thead>
              <tbody>
                {data.recentIncoming.map((r, i) => (
                  <tr key={i}>
                    <Td>{r["Report Type"]}</Td>
                    <Td>{r.LHO}</Td>
                    <Td><Badge tone="blue">{r.Status}</Badge></Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>

        <Card>
          <CardHeader title="Recent Outgoing" />
          {data.recentOutgoing.length === 0 ? (
            <EmptyState title="No outgoing mail yet" />
          ) : (
            <Table>
              <thead><tr><Th>Recipient</Th><Th>LHO</Th><Th>Status</Th></tr></thead>
              <tbody>
                {data.recentOutgoing.map((r, i) => (
                  <tr key={i}>
                    <Td>{r.Recipient}</Td>
                    <Td>{r.LHO}</Td>
                    <Td><Badge tone={r.Status === "Sent" ? "green" : "red"}>{r.Status}</Badge></Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </div>

      <div className="mt-6">
        <Card>
          <CardHeader title="Recent Distribution Jobs" />
          {data.recentJobs.length === 0 ? (
            <EmptyState title="No distribution jobs yet" subtitle="Turn on automation for a report in Scheduler to get started." />
          ) : (
            <Table>
              <thead><tr><Th>Report</Th><Th>Status</Th><Th>Recipients</Th><Th>Sent</Th><Th>Failed</Th></tr></thead>
              <tbody>
                {data.recentJobs.map((j) => (
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
    </div>
  );
}
