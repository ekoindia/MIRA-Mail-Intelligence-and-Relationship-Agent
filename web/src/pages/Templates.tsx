import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus, Save, Trash2, Star, Mail, Search, FileText, Users, Eye, Sparkles,
  ArrowUpRight, Inbox, Tag, Target,
} from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { PageHeader, Card, CardHeader, Button, Badge, EmptyState, LoadingBlock, Toggle } from "../components/ui";

interface MappedReportDetail { id: number; name: string; automated: boolean }
interface TemplateItem {
  id: number; name: string; subject: string; bodyHtml: string; isDefault: boolean;
  updatedAt: string; mappedReports: string[]; mappedReportIds: number[];
  mappedReportDetails: MappedReportDetail[]; isDigestManaged: boolean;
}
interface ReportOption { id: number; reportName: string; frequency: string | null; }

const EMPTY_FORM = {
  name: "", subject: "{{Report_Name}} - Report ({{Date}})",
  bodyHtml:
    "Dear {{Recipient_Name}},\n\nPlease find attached the {{Report_Name}} report dated {{Date}}.\n\nRegards,\nReports Distribution Team",
  isDefault: false, reportIds: [] as number[], isDigestManaged: false,
  mappedReportDetails: [] as MappedReportDetail[],
};

// Purely cosmetic grouping of the flat variable list (utils/helpers.py::SUPPORTED_TEMPLATE_VARS)
// so the "insert variable" panel reads as two short rows instead of one long unsorted one.
const RECIPIENT_VARS = new Set(["Recipient_Name", "Branch_Name", "RBO_Name", "AO_Name", "LHO_Name", "Corp_Name"]);

const inputClass =
  "w-full rounded-lg border border-ink-300 bg-white px-3 py-2 text-sm text-ink-900 shadow-sm transition-shadow focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100";

function FieldLabel({ children, hint }: { children: string; hint?: string }) {
  return (
    <div className="mb-1.5 flex items-baseline justify-between">
      <label className="text-xs font-semibold uppercase tracking-wide text-ink-500">{children}</label>
      {hint && <span className="text-[11px] text-ink-400">{hint}</span>}
    </div>
  );
}

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
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

// ============================================================
// Outgoing (report) templates — unchanged behavior, just moved
// into its own component so the page can host a second, separate
// section for incoming reply templates alongside it.
// ============================================================
function OutgoingTemplates() {
  const queryClient = useQueryClient();
  const { data: templates, isLoading } = useQuery<TemplateItem[]>({
    queryKey: ["templates"],
    queryFn: async () => (await api.get("/api/templates")).data,
  });
  const { data: reports } = useQuery<ReportOption[]>({
    queryKey: ["report-options"],
    queryFn: async () => (await api.get("/api/templates/report-options")).data,
  });
  const { data: variables } = useQuery<string[]>({
    queryKey: ["template-variables"],
    queryFn: async () => (await api.get("/api/templates/variables")).data,
  });

  const [selectedId, setSelectedId] = useState<number | "new" | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [preview, setPreview] = useState<{ subject: string; bodyHtml: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  function loadTemplate(t: TemplateItem) {
    setSelectedId(t.id);
    setForm({
      name: t.name, subject: t.subject, bodyHtml: t.bodyHtml, isDefault: t.isDefault,
      reportIds: t.mappedReportIds, isDigestManaged: t.isDigestManaged,
      mappedReportDetails: t.mappedReportDetails,
    });
    setError(null);
  }

  function newTemplate() {
    setSelectedId("new");
    setForm(EMPTY_FORM);
    setError(null);
  }

  const save = useMutation({
    mutationFn: async () => {
      // Digest-managed templates' "applies to" list is inferred by name
      // from the Reports Mapping config, not stored via ReportMaster.
      // default_template_id — sending the merged display list back would
      // wrongly create real FK associations that didn't exist before.
      const body = {
        name: form.name, subject: form.subject, bodyHtml: form.bodyHtml, isDefault: form.isDefault,
        reportIds: form.isDigestManaged ? [] : form.reportIds,
      };
      if (selectedId === "new" || selectedId === null) {
        return (await api.post("/api/templates", body)).data;
      }
      return (await api.put(`/api/templates/${selectedId}`, body)).data;
    },
    onSuccess: (data: TemplateItem) => {
      queryClient.invalidateQueries({ queryKey: ["templates"] });
      queryClient.invalidateQueries({ queryKey: ["reports"] });
      setSelectedId(data.id);
      setError(null);
    },
    onError: (err) => setError(apiErrorMessage(err, "Couldn't save this template.")),
  });

  const remove = useMutation({
    mutationFn: async () => api.delete(`/api/templates/${selectedId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["templates"] });
      queryClient.invalidateQueries({ queryKey: ["reports"] });
      newTemplate();
    },
  });

  useEffect(() => {
    const timeout = setTimeout(() => {
      if (!form.subject && !form.bodyHtml) return;
      api.post("/api/templates/preview", { subject: form.subject, bodyHtml: form.bodyHtml })
        .then((res) => setPreview(res.data))
        .catch(() => {});
    }, 250);
    return () => clearTimeout(timeout);
  }, [form.subject, form.bodyHtml]);

  const filteredTemplates = useMemo(() => {
    if (!templates) return [];
    const q = search.trim().toLowerCase();
    if (!q) return templates;
    return templates.filter((t) => t.name.toLowerCase().includes(q));
  }, [templates, search]);

  const recipientVars = (variables ?? []).filter((v) => RECIPIENT_VARS.has(v));
  const otherVars = (variables ?? []).filter((v) => !RECIPIENT_VARS.has(v));
  const selectedTemplate = typeof selectedId === "number" ? templates?.find((t) => t.id === selectedId) : undefined;

  if (isLoading) return <LoadingBlock />;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[280px_1fr]">
      <Card className="h-fit overflow-hidden">
        <div className="flex items-center justify-between border-b border-ink-100 px-4 pt-3.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-ink-500">All Templates</span>
          <div className="flex items-center gap-1.5">
            <span className="rounded-full bg-ink-100 px-2 py-0.5 text-[11px] font-semibold text-ink-500">
              {templates?.length ?? 0}
            </span>
            <button
              onClick={newTemplate}
              title="New template"
              className="flex h-6 w-6 items-center justify-center rounded-md text-ink-500 hover:bg-ink-100 hover:text-brand-700"
            >
              <Plus className="h-4 w-4" strokeWidth={2.25} />
            </button>
          </div>
        </div>
        <div className="border-b border-ink-100 p-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-400" strokeWidth={2.25} />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search templates…"
              className="w-full rounded-lg border border-ink-200 bg-ink-50 py-1.5 pl-8 pr-2.5 text-xs text-ink-700 focus:border-brand-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          </div>
        </div>

        {!templates || templates.length === 0 ? (
          <EmptyState title="No templates yet" icon={Mail} />
        ) : filteredTemplates.length === 0 ? (
          <EmptyState title="No matches" subtitle={`Nothing named "${search}"`} icon={Search} />
        ) : (
          <ul className="max-h-[70vh] divide-y divide-ink-100 overflow-y-auto">
            {filteredTemplates.map((t) => {
              const active = selectedId === t.id;
              return (
                <li key={t.id} className="relative">
                  {active && <span className="absolute inset-y-0 left-0 w-0.5 bg-brand-600" />}
                  <button
                    onClick={() => loadTemplate(t)}
                    className={`flex w-full items-start gap-2.5 px-4 py-3 text-left transition-colors duration-150 ${
                      active ? "bg-brand-50" : "hover:bg-ink-50"
                    }`}
                  >
                    <div
                      className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${
                        active ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-400"
                      }`}
                    >
                      <FileText className="h-3.5 w-3.5" strokeWidth={2.25} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className={`truncate text-sm font-medium ${active ? "text-brand-800" : "text-ink-900"}`}>
                          {t.name}
                        </span>
                        {t.isDefault && <Star className="h-3 w-3 shrink-0 fill-amber-400 text-amber-400" />}
                      </div>
                      <span className="truncate text-xs text-ink-400">
                        {t.mappedReports.length > 0 ? `${t.mappedReports.length} report(s)` : "Not attached"}
                        {" · "}{timeAgo(t.updatedAt)}
                      </span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {selectedId === null ? (
        <Card className="flex items-center justify-center py-20">
          <EmptyState title="Select a template to edit" subtitle="Or create a new one." icon={Mail} />
        </Card>
      ) : (
        <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="space-y-5">
            <Card>
              <CardHeader
                title="Template details"
                subtitle="Name it and choose which reports it applies to."
                action={
                  <div className="flex items-center gap-2">
                    {form.isDefault && <Badge tone="amber">Default</Badge>}
                    {selectedTemplate && (
                      <span className="text-xs text-ink-400">Updated {timeAgo(selectedTemplate.updatedAt)}</span>
                    )}
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
                      <Users className="h-4 w-4" strokeWidth={2.25} />
                    </div>
                  </div>
                }
              />
              <div className="p-5">
                <FieldLabel>Template Name</FieldLabel>
                <input
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="e.g. Daily RBO Update"
                  spellCheck
                  lang="en"
                  className={inputClass}
                />

                <div className="mt-4">
                  <FieldLabel>Applies to Reports</FieldLabel>
                  {form.isDigestManaged && (
                    <p className="mb-1.5 text-xs text-ink-400">
                      Determined automatically by the Reports page's mapping (one report can feed several levels'
                      combined emails at once) — not editable here.
                    </p>
                  )}
                  <div className="flex flex-wrap gap-1.5">
                    {form.isDigestManaged
                      ? form.mappedReportDetails.map((r) => (
                          <span
                            key={r.id}
                            className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                              r.automated
                                ? "border-brand-600 bg-brand-600 text-white"
                                : "border-ink-200 bg-ink-100 text-ink-500"
                            }`}
                          >
                            {r.name}{!r.automated && " (paused)"}
                          </span>
                        ))
                      : (reports ?? []).map((r) => {
                          const active = form.reportIds.includes(r.id);
                          return (
                            <button
                              key={r.id}
                              onClick={() =>
                                setForm({
                                  ...form,
                                  reportIds: active
                                    ? form.reportIds.filter((id) => id !== r.id)
                                    : [...form.reportIds, r.id],
                                })
                              }
                              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                                active
                                  ? "border-brand-600 bg-brand-600 text-white"
                                  : "border-ink-200 text-ink-600 hover:border-brand-300 hover:text-brand-700"
                              }`}
                            >
                              {r.reportName}
                            </button>
                          );
                        })}
                  </div>
                </div>

                <label className="mt-4 flex items-center gap-2 text-sm text-ink-700">
                  <input
                    type="checkbox"
                    checked={form.isDefault}
                    onChange={(e) => setForm({ ...form, isDefault: e.target.checked })}
                    className="h-4 w-4 rounded border-ink-300 text-brand-600 focus:ring-brand-500"
                  />
                  Set as global fallback template
                </label>
              </div>
            </Card>

            <Card>
              <CardHeader
                title="Message"
                subtitle="Plain text — line breaks are kept automatically, no HTML needed."
                action={
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
                    <Sparkles className="h-4 w-4" strokeWidth={2.25} />
                  </div>
                }
              />
              <div className="p-5">
                <FieldLabel>Subject</FieldLabel>
                <input
                  value={form.subject}
                  onChange={(e) => setForm({ ...form, subject: e.target.value })}
                  spellCheck
                  lang="en"
                  className={inputClass}
                />

                <div className="mt-4">
                  <FieldLabel hint={`${form.bodyHtml.length.toLocaleString()} characters`}>Body</FieldLabel>
                  <textarea
                    value={form.bodyHtml}
                    onChange={(e) => setForm({ ...form, bodyHtml: e.target.value })}
                    rows={11}
                    spellCheck
                    lang="en"
                    className={`${inputClass} font-mono leading-relaxed`}
                  />
                </div>

                <div className="mt-4 rounded-lg border border-info-line bg-info-soft p-3">
                  <p className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-info-fg">
                    <Sparkles className="h-3 w-3" strokeWidth={2.5} />
                    Insert a variable
                  </p>
                  {recipientVars.length > 0 && (
                    <div className="mb-2 flex flex-wrap items-center gap-1.5">
                      <span className="text-[10px] font-medium uppercase tracking-wide text-brand-700/70">Recipient</span>
                      {recipientVars.map((v) => (
                        <button
                          key={v}
                          onClick={() => setForm({ ...form, bodyHtml: form.bodyHtml + `{{${v}}}` })}
                          className="rounded-md border border-brand-200 bg-white px-2 py-1 font-mono text-[11px] font-medium text-brand-700 shadow-sm transition-colors hover:border-brand-400 hover:bg-brand-100"
                          title="Click to insert into body"
                        >
                          {`{{${v}}}`}
                        </button>
                      ))}
                    </div>
                  )}
                  {otherVars.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-[10px] font-medium uppercase tracking-wide text-info-fg/70">Report &amp; date</span>
                      {otherVars.map((v) => (
                        <button
                          key={v}
                          onClick={() => setForm({ ...form, bodyHtml: form.bodyHtml + `{{${v}}}` })}
                          className="rounded-md border border-info-line bg-white px-2 py-1 font-mono text-[11px] font-medium text-info-fg shadow-sm transition-colors hover:bg-info-soft"
                          title="Click to insert into body"
                        >
                          {`{{${v}}}`}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </Card>

            {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}

            <div className="flex gap-2">
              <Button onClick={() => save.mutate()} disabled={save.isPending || !form.name.trim() || !form.subject.trim()}>
                <Save className="h-3.5 w-3.5" strokeWidth={2.25} />
                {save.isPending ? "Saving..." : "Save Template"}
              </Button>
              {typeof selectedId === "number" && (
                <Button
                  variant="danger"
                  onClick={() => {
                    if (window.confirm(`Delete "${form.name}"? This can't be undone.`)) remove.mutate();
                  }}
                  disabled={remove.isPending}
                >
                  <Trash2 className="h-3.5 w-3.5" strokeWidth={2.25} />
                  Delete
                </Button>
              )}
            </div>
          </div>

          <div className="xl:sticky xl:top-5">
            <Card className="overflow-hidden">
              <div className="flex items-center gap-2 border-b border-brand-100 bg-brand-50 px-5 py-3">
                <Eye className="h-4 w-4 text-brand-600" strokeWidth={2.25} />
                <span className="text-sm font-semibold text-brand-800">Live Preview</span>
                <span className="ml-auto text-[11px] font-medium text-brand-600/70">as recipients see it</span>
              </div>
              <div className="bg-ink-100 p-4">
                <div className="overflow-hidden rounded-lg border border-ink-200 bg-white shadow-sm">
                  <div className="border-t-2 border-t-brand-500 px-4 py-3">
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-400">Subject</div>
                    <div className="mt-0.5 truncate text-sm font-semibold text-ink-900">
                      {preview?.subject || <span className="text-ink-300">—</span>}
                    </div>
                  </div>
                  <div className="border-t border-ink-100" />
                  <div
                    className="max-h-[70vh] overflow-y-auto overflow-x-auto px-4 py-4 text-sm leading-relaxed text-ink-800"
                    dangerouslySetInnerHTML={{ __html: preview?.bodyHtml ?? "" }}
                  />
                </div>
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// Incoming reply templates — detect-and-suggest only. Saving a
// template here never drafts or sends anything; it's a keyword
// -> canned-reply library plus how many past incoming emails
// each one would have matched, for visibility.
// ============================================================
interface ReplyTemplateItem {
  id: number; categoryName: string; matchKeywords: string;
  subjectTemplate: string; bodyTemplate: string; isActive: boolean; updatedAt: string;
}
interface MatchSummary {
  direct_total: number; matched_total: number; matched_pct: number;
  by_template: { template_id: number; category_name: string; matched_count: number; avg_confidence: number }[];
}

const EMPTY_REPLY_FORM = {
  categoryName: "", matchKeywords: "", subjectTemplate: "Re: ",
  bodyTemplate: "<p>Dear Sir/Ma'am,</p><p></p><p>Regards,<br />Operations Team</p>", isActive: true,
};

function IncomingReplyTemplates() {
  const queryClient = useQueryClient();
  const { data: templates, isLoading } = useQuery<ReplyTemplateItem[]>({
    queryKey: ["incoming-reply-templates"],
    queryFn: async () => (await api.get("/api/incoming/reply-templates")).data,
  });
  const { data: matchSummary } = useQuery<MatchSummary>({
    queryKey: ["incoming-reply-match-summary"],
    queryFn: async () => (await api.get("/api/incoming/reply-template-match-summary")).data,
  });

  const [selectedId, setSelectedId] = useState<number | "new" | null>(null);
  const [form, setForm] = useState(EMPTY_REPLY_FORM);
  const [error, setError] = useState<string | null>(null);

  function loadTemplate(t: ReplyTemplateItem) {
    setSelectedId(t.id);
    setForm({
      categoryName: t.categoryName, matchKeywords: t.matchKeywords,
      subjectTemplate: t.subjectTemplate, bodyTemplate: t.bodyTemplate, isActive: t.isActive,
    });
    setError(null);
  }

  function newTemplate() {
    setSelectedId("new");
    setForm(EMPTY_REPLY_FORM);
    setError(null);
  }

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["incoming-reply-templates"] });
    queryClient.invalidateQueries({ queryKey: ["incoming-reply-match-summary"] });
    queryClient.invalidateQueries({ queryKey: ["incoming-patterns"] });
    queryClient.invalidateQueries({ queryKey: ["incoming-summary"] });
  };

  const save = useMutation({
    mutationFn: async () => {
      if (selectedId === "new" || selectedId === null) {
        return (await api.post("/api/incoming/reply-templates", form)).data;
      }
      return (await api.put(`/api/incoming/reply-templates/${selectedId}`, form)).data;
    },
    onSuccess: (data: ReplyTemplateItem) => {
      invalidate();
      setSelectedId(data.id);
      setError(null);
    },
    onError: (err) => setError(apiErrorMessage(err, "Couldn't save this reply template.")),
  });

  const remove = useMutation({
    mutationFn: async () => api.delete(`/api/incoming/reply-templates/${selectedId}`),
    onSuccess: () => {
      invalidate();
      newTemplate();
    },
  });

  const selectedMatch = typeof selectedId === "number"
    ? matchSummary?.by_template.find((m) => m.template_id === selectedId) : undefined;

  if (isLoading) return <LoadingBlock />;

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[280px_1fr]">
      <Card className="h-fit overflow-hidden">
        <div className="flex items-center justify-between border-b border-ink-100 px-4 pt-3.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-ink-500">Reply Categories</span>
          <div className="flex items-center gap-1.5">
            <span className="rounded-full bg-ink-100 px-2 py-0.5 text-[11px] font-semibold text-ink-500">
              {templates?.length ?? 0}
            </span>
            <button
              onClick={newTemplate}
              title="New reply category"
              className="flex h-6 w-6 items-center justify-center rounded-md text-ink-500 hover:bg-ink-100 hover:text-brand-700"
            >
              <Plus className="h-4 w-4" strokeWidth={2.25} />
            </button>
          </div>
        </div>

        {matchSummary && (
          <div className="border-b border-ink-100 px-4 py-3">
            <div className="text-2xl font-semibold tabular-nums text-ink-900">
              {Math.round(matchSummary.matched_pct * 100)}%
            </div>
            <div className="text-xs text-ink-500">
              {matchSummary.matched_total} of {matchSummary.direct_total} direct emails match a category
            </div>
          </div>
        )}

        {!templates || templates.length === 0 ? (
          <EmptyState title="No reply categories yet" subtitle="Create one to start scoring matches." icon={Inbox} />
        ) : (
          <ul className="max-h-[60vh] divide-y divide-ink-100 overflow-y-auto">
            {templates.map((t) => {
              const active = selectedId === t.id;
              const match = matchSummary?.by_template.find((m) => m.template_id === t.id);
              return (
                <li key={t.id} className="relative">
                  {active && <span className="absolute inset-y-0 left-0 w-0.5 bg-brand-600" />}
                  <button
                    onClick={() => loadTemplate(t)}
                    className={`flex w-full items-start gap-2.5 px-4 py-3 text-left transition-colors duration-150 ${
                      active ? "bg-brand-50" : "hover:bg-ink-50"
                    }`}
                  >
                    <div
                      className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${
                        active ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-400"
                      }`}
                    >
                      <Tag className="h-3.5 w-3.5" strokeWidth={2.25} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className={`truncate text-sm font-medium ${active ? "text-brand-800" : "text-ink-900"}`}>
                          {t.categoryName}
                        </span>
                        {!t.isActive && <Badge tone="slate">Inactive</Badge>}
                      </div>
                      <span className="truncate text-xs text-ink-400">
                        {match ? `${match.matched_count} matched` : "0 matched"} · {timeAgo(t.updatedAt)}
                      </span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {selectedId === null ? (
        <Card className="flex items-center justify-center py-20">
          <EmptyState title="Select a reply category to edit" subtitle="Or create a new one." icon={Inbox} />
        </Card>
      ) : (
        <div className="space-y-5">
          <Card>
            <CardHeader
              title="Reply category"
              subtitle="Keyword-matched against incoming subject + body — plain substring matching, not an AI model."
              action={
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
                  <Target className="h-4 w-4" strokeWidth={2.25} />
                </div>
              }
            />
            <div className="p-5">
              <FieldLabel>Category Name</FieldLabel>
              <input
                value={form.categoryName}
                onChange={(e) => setForm({ ...form, categoryName: e.target.value })}
                placeholder="e.g. Terminal Reset Request"
                spellCheck
                lang="en"
                className={inputClass}
              />

              <div className="mt-4">
                <FieldLabel hint="comma-separated">Match Keywords</FieldLabel>
                <input
                  value={form.matchKeywords}
                  onChange={(e) => setForm({ ...form, matchKeywords: e.target.value })}
                  placeholder="e.g. terminal reset, reset the terminal"
                  spellCheck
                  lang="en"
                  className={`${inputClass} font-mono`}
                />
                <p className="mt-1.5 text-xs text-ink-400">
                  An email matches this category if its subject or body contains any of these phrases (case-insensitive).
                </p>
              </div>

              {selectedMatch && (
                <div className="mt-4 rounded-lg border border-good-line bg-good-soft px-3 py-2 text-xs text-good-fg">
                  Matched <b>{selectedMatch.matched_count}</b> incoming email(s) so far, avg confidence{" "}
                  <b>{Math.round(selectedMatch.avg_confidence * 100)}%</b>.
                </div>
              )}

              <label className="mt-4 flex items-center gap-2 text-sm text-ink-700">
                <Toggle checked={form.isActive} onChange={() => setForm({ ...form, isActive: !form.isActive })} />
                Active (inactive categories are never matched)
              </label>
            </div>
          </Card>

          <Card>
            <CardHeader
              title="Reply content"
              subtitle="Saved here only — nothing drafts or sends automatically yet."
              action={
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
                  <Sparkles className="h-4 w-4" strokeWidth={2.25} />
                </div>
              }
            />
            <div className="p-5">
              <FieldLabel>Subject</FieldLabel>
              <input
                value={form.subjectTemplate}
                onChange={(e) => setForm({ ...form, subjectTemplate: e.target.value })}
                spellCheck
                lang="en"
                className={inputClass}
              />

              <div className="mt-4">
                <FieldLabel hint={`${form.bodyTemplate.length.toLocaleString()} characters`}>Body</FieldLabel>
                <textarea
                  value={form.bodyTemplate}
                  onChange={(e) => setForm({ ...form, bodyTemplate: e.target.value })}
                  rows={8}
                  spellCheck
                  lang="en"
                  className={`${inputClass} font-mono leading-relaxed`}
                />
              </div>
            </div>
          </Card>

          {error && <div className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}

          <div className="flex gap-2">
            <Button onClick={() => save.mutate()} disabled={save.isPending || !form.categoryName.trim() || !form.matchKeywords.trim()}>
              <Save className="h-3.5 w-3.5" strokeWidth={2.25} />
              {save.isPending ? "Saving..." : "Save Category"}
            </Button>
            {typeof selectedId === "number" && (
              <Button
                variant="danger"
                onClick={() => {
                  if (window.confirm(`Delete "${form.categoryName}"? This can't be undone.`)) remove.mutate();
                }}
                disabled={remove.isPending}
              >
                <Trash2 className="h-3.5 w-3.5" strokeWidth={2.25} />
                Delete
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function Templates() {
  const [view, setView] = useState<"outgoing" | "incoming">("outgoing");

  return (
    <div>
      <PageHeader
        title="Templates"
        subtitle={
          view === "outgoing"
            ? "Define reusable email templates once, then attach them to every report that should use the same wording."
            : "Recurring incoming-mail request types and their canned replies — matched by keyword, saved here, never auto-sent."
        }
        action={<ViewToggle view={view} onChange={setView} />}
      />

      {view === "outgoing" ? <OutgoingTemplates /> : <IncomingReplyTemplates />}
    </div>
  );
}
