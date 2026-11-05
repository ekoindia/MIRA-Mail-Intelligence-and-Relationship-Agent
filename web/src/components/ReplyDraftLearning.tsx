import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Card, CardHeader, Badge, EmptyState, LoadingBlock, Table, Th, Td } from "./ui";

type Outcome = "pending" | "sent_as_is" | "edited" | "replied_separately" | "not_used";

interface TemplateRow {
  template: string; total: number; asIsRate: number | null;
  pending: number; sent_as_is: number; edited: number; replied_separately: number; not_used: number;
}
interface Summary { templates: TemplateRow[]; totals: Omit<TemplateRow, "template"> }
interface DetailRow {
  id: number; subject: string | null; sender: string | null; template: string | null; outcome: Outcome;
  similarity: number | null; draftedAt: string | null; sentAt: string | null; backfilled: boolean;
  added: string[]; removed: string[];
}
interface Detail { total: number; page: number; pageSize: number; rows: DetailRow[] }

const OUTCOME_META: Record<Outcome, { label: string; tone: "green" | "amber" | "blue" | "slate" | "red" }> = {
  sent_as_is: { label: "Sent as-is", tone: "green" },
  edited: { label: "Edited, then sent", tone: "amber" },
  replied_separately: { label: "Replied separately", tone: "red" },
  not_used: { label: "Not used", tone: "slate" },
  pending: { label: "Waiting", tone: "blue" },
};
const OUTCOME_ORDER: Outcome[] = ["sent_as_is", "edited", "replied_separately", "not_used", "pending"];

function pct(n: number | null): string {
  return n == null ? "—" : `${Math.round(n * 100)}%`;
}

function fmt(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: true,
  });
}

function Tile({ label, value, sub, active, onClick }: {
  label: string; value: string | number; sub?: string; active?: boolean; onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${
        active ? "border-brand-300 bg-brand-50 ring-1 ring-brand-200" : "border-ink-200 bg-white hover:bg-ink-50"
      }`}
    >
      <div className="text-[11px] font-medium uppercase tracking-wide text-ink-500">{label}</div>
      <div className="mt-1 font-mono text-lg font-semibold text-ink-900">{value}</div>
      {sub && <div className="text-[11px] text-ink-500">{sub}</div>}
    </button>
  );
}

export default function ReplyDraftLearning() {
  const [template, setTemplate] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [page, setPage] = useState(1);
  const [openRow, setOpenRow] = useState<number | null>(null);
  const pageSize = 15;

  const { data: summary, isLoading } = useQuery<Summary>({
    queryKey: ["reply-drafts-summary"],
    queryFn: async () => (await api.get("/api/incoming/reply-drafts/summary")).data,
  });
  const { data: detail, isLoading: detailLoading } = useQuery<Detail>({
    queryKey: ["reply-drafts-detail", template, outcome, page],
    queryFn: async () => (
      await api.get("/api/incoming/reply-drafts/detail", {
        params: { template: template ?? undefined, outcome: outcome ?? undefined, page, pageSize },
      })
    ).data,
  });

  const t = summary?.totals;
  const decided = t ? t.sent_as_is + t.edited + t.replied_separately : 0;
  const totalPages = detail ? Math.max(1, Math.ceil(detail.total / pageSize)) : 1;

  return (
    <Card>
      <CardHeader
        title="Reply Drafts — What Happened to Them"
        subtitle="Every acknowledgment draft compared with the reply actually sent on its thread: sent as-is, edited, replied separately, or not used. Measurement only — nothing here drafts, sends, or changes a template."
      />
      {isLoading || !summary || !t ? (
        <LoadingBlock />
      ) : t.total === 0 ? (
        <EmptyState
          title="No reply drafts recorded yet"
          subtitle="Drafts appear here once acknowledgment drafting runs and the Sent folder has been scanned."
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 px-5 pt-4 sm:grid-cols-3 lg:grid-cols-6">
            <Tile label="Drafts" value={t.total} active={outcome === null} onClick={() => { setOutcome(null); setPage(1); }} />
            {OUTCOME_ORDER.map((o) => (
              <Tile
                key={o}
                label={OUTCOME_META[o].label}
                value={t[o]}
                sub={o === "sent_as_is" ? `${pct(t.asIsRate)} of ${decided} sent` : undefined}
                active={outcome === o}
                onClick={() => { setOutcome(outcome === o ? null : o); setPage(1); }}
              />
            ))}
          </div>

          <div className="mx-5 mt-4 overflow-hidden rounded-lg border border-ink-200 bg-white">
            <Table>
              <thead>
                <tr>
                  <Th>Template</Th><Th>Drafts</Th><Th>Sent as-is</Th><Th>Edited</Th>
                  <Th>Replied separately</Th><Th>Not used</Th><Th>Waiting</Th><Th>As-is rate</Th>
                </tr>
              </thead>
              <tbody>
                {summary.templates.map((row) => (
                  <tr
                    key={row.template}
                    onClick={() => { setTemplate(template === row.template ? null : row.template); setPage(1); }}
                    className={`cursor-pointer ${template === row.template ? "bg-brand-50" : "hover:bg-ink-50"}`}
                  >
                    <Td className="font-medium text-ink-900">{row.template}</Td>
                    <Td>{row.total}</Td><Td>{row.sent_as_is}</Td><Td>{row.edited}</Td>
                    <Td>{row.replied_separately}</Td><Td>{row.not_used}</Td><Td>{row.pending}</Td>
                    <Td className="font-semibold">{pct(row.asIsRate)}</Td>
                  </tr>
                ))}
              </tbody>
            </Table>
          </div>

          <div className="mx-5 mb-5 mt-4">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-ink-500">
              <span>
                Showing {detail?.total ?? 0} draft{detail?.total === 1 ? "" : "s"}
                {template && <> for <b className="text-ink-700">{template}</b></>}
                {outcome && <> · <b className="text-ink-700">{OUTCOME_META[outcome].label}</b></>}
              </span>
              {(template || outcome) && (
                <button
                  type="button"
                  className="rounded-md px-2 py-0.5 font-medium text-brand-700 hover:bg-brand-50"
                  onClick={() => { setTemplate(null); setOutcome(null); setPage(1); }}
                >
                  Clear filters
                </button>
              )}
            </div>

            {detailLoading || !detail ? (
              <LoadingBlock />
            ) : detail.rows.length === 0 ? (
              <EmptyState title="No drafts match these filters" />
            ) : (
              <div className="overflow-hidden rounded-lg border border-ink-200 bg-white">
                <Table>
                  <thead>
                    <tr><Th>Drafted</Th><Th>Email</Th><Th>Template</Th><Th>Outcome</Th><Th>Match</Th><Th>Sent</Th></tr>
                  </thead>
                  <tbody>
                    {detail.rows.map((r) => {
                      const changed = r.added.length > 0 || r.removed.length > 0;
                      const open = openRow === r.id;
                      return (
                        <Fragment key={r.id}>
                          <tr
                            onClick={() => changed && setOpenRow(open ? null : r.id)}
                            className={changed ? "cursor-pointer hover:bg-ink-50" : undefined}
                          >
                            <Td className="whitespace-nowrap text-ink-500">
                              {fmt(r.draftedAt)}
                              {r.backfilled && <div className="text-[10px] text-ink-400">reconstructed</div>}
                            </Td>
                            <Td>
                              <div className="font-medium text-ink-900">{r.subject ?? "(no subject)"}</div>
                              <div className="text-xs text-ink-400">{r.sender}</div>
                            </Td>
                            <Td className="text-ink-600">{r.template}</Td>
                            <Td>
                              <Badge tone={OUTCOME_META[r.outcome].tone}>{OUTCOME_META[r.outcome].label}</Badge>
                              {changed && <div className="mt-1 text-[11px] text-brand-700">{open ? "Hide changes" : "Show changes"}</div>}
                            </Td>
                            <Td className="text-ink-600">{pct(r.similarity)}</Td>
                            <Td className="whitespace-nowrap text-ink-500">{fmt(r.sentAt)}</Td>
                          </tr>
                          {open && (
                            <tr>
                              <td colSpan={6} className="bg-ink-50 px-4 py-3 text-xs">
                                {r.added.length > 0 && (
                                  <div className="mb-2">
                                    <div className="mb-1 font-semibold text-good-fg">Added by the person who sent it</div>
                                    {r.added.map((a, i) => (
                                      <div key={i} className="rounded bg-good-soft px-2 py-1 text-good-fg">+ {a}</div>
                                    ))}
                                  </div>
                                )}
                                {r.removed.length > 0 && (
                                  <div>
                                    <div className="mb-1 font-semibold text-rose-700">Removed from the draft</div>
                                    {r.removed.map((a, i) => (
                                      <div key={i} className="rounded bg-rose-50 px-2 py-1 text-rose-700 line-through">− {a}</div>
                                    ))}
                                  </div>
                                )}
                              </td>
                            </tr>
                          )}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </Table>
                {totalPages > 1 && (
                  <div className="flex items-center justify-between border-t border-ink-200 px-4 py-2.5 text-xs text-ink-500">
                    <span>Page {page} of {totalPages}</span>
                    <div className="flex gap-1.5">
                      <button
                        type="button"
                        onClick={() => setPage((p) => Math.max(1, p - 1))}
                        disabled={page <= 1}
                        className="rounded-md border border-ink-200 px-2.5 py-1 font-medium hover:bg-ink-50 disabled:opacity-40"
                      >
                        Prev
                      </button>
                      <button
                        type="button"
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
            )}
          </div>
        </>
      )}
    </Card>
  );
}
