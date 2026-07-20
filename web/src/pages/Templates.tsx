import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Save, Trash2, Star, Mail } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { PageHeader, Card, Button, EmptyState, LoadingBlock } from "../components/ui";

interface TemplateItem {
  id: number; name: string; subject: string; bodyHtml: string; isDefault: boolean;
  updatedAt: string; mappedReports: string[];
}
interface ReportOption { id: number; reportName: string; }

const EMPTY_FORM = {
  name: "", subject: "{{Report_Name}} - Report ({{Date}})",
  bodyHtml:
    "<p>Dear {{Recipient_Name}},</p>\n<p>Please find attached the <b>{{Report_Name}}</b> report dated {{Date}}.</p>\n<p>Regards,<br/>Reports Distribution Team</p>",
  isDefault: false, reportIds: [] as number[],
};

export default function Templates() {
  const queryClient = useQueryClient();
  const { data: templates, isLoading } = useQuery<TemplateItem[]>({
    queryKey: ["templates"],
    queryFn: async () => (await api.get("/api/templates")).data,
  });
  const { data: reports } = useQuery<ReportOption[]>({
    queryKey: ["reports"],
    queryFn: async () => (await api.get("/api/reports")).data,
  });
  const { data: variables } = useQuery<string[]>({
    queryKey: ["template-variables"],
    queryFn: async () => (await api.get("/api/templates/variables")).data,
  });

  const [selectedId, setSelectedId] = useState<number | "new" | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const [preview, setPreview] = useState<{ subject: string; bodyHtml: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reportsById = new Map((reports ?? []).map((r) => [r.reportName, r.id]));

  function loadTemplate(t: TemplateItem) {
    setSelectedId(t.id);
    setForm({
      name: t.name, subject: t.subject, bodyHtml: t.bodyHtml, isDefault: t.isDefault,
      reportIds: t.mappedReports.map((name) => reportsById.get(name)).filter((x): x is number => !!x),
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
      const body = { name: form.name, subject: form.subject, bodyHtml: form.bodyHtml, isDefault: form.isDefault, reportIds: form.reportIds };
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

  if (isLoading) return <LoadingBlock />;

  return (
    <div>
      <PageHeader
        title="Templates"
        subtitle="Define reusable email templates once, then attach them to every report that should use the same wording."
        action={<Button onClick={newTemplate}><Plus className="h-4 w-4" strokeWidth={2.5} />New Template</Button>}
      />

      <div className="grid grid-cols-[280px_1fr] gap-6">
        <Card className="h-fit">
          {!templates || templates.length === 0 ? (
            <EmptyState title="No templates yet" icon={Mail} />
          ) : (
            <ul className="divide-y divide-ink-100">
              {templates.map((t) => (
                <li key={t.id}>
                  <button
                    onClick={() => loadTemplate(t)}
                    className={`flex w-full flex-col items-start gap-0.5 px-4 py-3 text-left transition-colors ${
                      selectedId === t.id ? "bg-brand-50" : "hover:bg-ink-50"
                    }`}
                  >
                    <div className="flex w-full items-center gap-1.5">
                      <span className="truncate text-sm font-medium text-ink-900">{t.name}</span>
                      {t.isDefault && <Star className="h-3 w-3 shrink-0 fill-amber-400 text-amber-400" />}
                    </div>
                    <span className="truncate text-xs text-ink-400">
                      {t.mappedReports.length > 0 ? `${t.mappedReports.length} report(s)` : "Not attached"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {selectedId === null ? (
          <Card className="flex items-center justify-center py-20">
            <EmptyState title="Select a template to edit" subtitle="Or create a new one." icon={Mail} />
          </Card>
        ) : (
          <div className="space-y-5">
            <Card className="p-5">
              <label className="mb-1.5 block text-xs font-medium text-ink-500">Template Name</label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="e.g. Daily RBO Update"
                className="w-full rounded-lg border border-ink-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              />

              <label className="mb-1.5 mt-4 block text-xs font-medium text-ink-500">Applies to Reports</label>
              <div className="flex flex-wrap gap-1.5">
                {(reports ?? []).map((r) => {
                  const active = form.reportIds.includes(r.id);
                  return (
                    <button
                      key={r.id}
                      onClick={() =>
                        setForm({
                          ...form,
                          reportIds: active ? form.reportIds.filter((id) => id !== r.id) : [...form.reportIds, r.id],
                        })
                      }
                      className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                        active ? "border-brand-600 bg-brand-600 text-white" : "border-ink-200 text-ink-600 hover:border-ink-300"
                      }`}
                    >
                      {r.reportName}
                    </button>
                  );
                })}
              </div>

              <label className="mb-1.5 mt-4 block text-xs font-medium text-ink-500">Subject</label>
              <input
                value={form.subject}
                onChange={(e) => setForm({ ...form, subject: e.target.value })}
                className="w-full rounded-lg border border-ink-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              />

              <label className="mb-1.5 mt-4 block text-xs font-medium text-ink-500">Body (HTML)</label>
              <textarea
                value={form.bodyHtml}
                onChange={(e) => setForm({ ...form, bodyHtml: e.target.value })}
                rows={9}
                className="w-full rounded-lg border border-ink-300 px-3 py-2 font-mono text-xs focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              />

              <div className="mt-3 flex flex-wrap gap-1">
                {(variables ?? []).map((v) => (
                  <button
                    key={v}
                    onClick={() => setForm({ ...form, bodyHtml: form.bodyHtml + `{{${v}}}` })}
                    className="rounded bg-ink-100 px-2 py-0.5 font-mono text-[11px] text-ink-600 hover:bg-ink-200"
                    title="Click to insert into body"
                  >
                    {`{{${v}}}`}
                  </button>
                ))}
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

              {error && <div className="mt-4 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}

              <div className="mt-5 flex gap-2">
                <Button onClick={() => save.mutate()} disabled={save.isPending || !form.name.trim() || !form.subject.trim()}>
                  <Save className="h-3.5 w-3.5" strokeWidth={2.25} />
                  {save.isPending ? "Saving..." : "Save Template"}
                </Button>
                {typeof selectedId === "number" && (
                  <Button variant="danger" onClick={() => remove.mutate()} disabled={remove.isPending}>
                    <Trash2 className="h-3.5 w-3.5" strokeWidth={2.25} />
                    Delete
                  </Button>
                )}
              </div>
            </Card>

            <Card>
              <div className="border-b border-ink-100 px-5 py-3 text-sm font-semibold text-ink-900">Live Preview</div>
              <div className="p-5">
                <div className="mb-3 text-sm"><span className="font-medium text-ink-500">Subject: </span>{preview?.subject}</div>
                <div className="rounded-lg border border-ink-100 bg-ink-50 p-4 text-sm text-ink-800" dangerouslySetInnerHTML={{ __html: preview?.bodyHtml ?? "" }} />
              </div>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
