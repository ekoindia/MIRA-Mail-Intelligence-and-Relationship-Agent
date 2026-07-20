import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail, CheckCircle2, XCircle, Loader2, Users, Plus, Trash2, Pencil } from "lucide-react";
import { api, apiErrorMessage } from "../lib/api";
import { PageHeader, Card, Button, Table, Th, Td, Badge, EmptyState } from "../components/ui";

interface GmailStatus {
  connected: boolean; email: string | null; error: string | null;
}

interface OrgUnit {
  id: number; level: string; unitName: string; email: string; ccEmails: string | null;
}

const LEVELS = ["RBO", "LHO", "AO", "Corporate Center", "Branch"] as const;

function CcCell({ unit, onSaved }: { unit: OrgUnit; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(unit.ccEmails ?? "");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => (await api.put(`/api/org-units/${unit.id}`, { ccEmails: value })).data,
    onSuccess: () => {
      setEditing(false);
      setError(null);
      onSaved();
    },
    onError: (err) => setError(apiErrorMessage(err, "Couldn't save.")),
  });

  if (!editing) {
    return (
      <button
        onClick={() => setEditing(true)}
        className="flex items-center gap-1.5 text-left text-xs text-ink-500 hover:text-brand-600"
        title="Edit CC list"
      >
        <span className="max-w-xs truncate">{unit.ccEmails || "No CC"}</span>
        <Pencil className="h-3 w-3 shrink-0" strokeWidth={2.25} />
      </button>
    );
  }
  return (
    <div className="flex items-center gap-1.5">
      <input
        autoFocus value={value} onChange={(e) => setValue(e.target.value)}
        placeholder="cc1@x.com, cc2@x.com"
        onKeyDown={(e) => e.key === "Enter" && save.mutate()}
        className="w-56 rounded-md border border-ink-300 px-2 py-1 text-xs focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
      />
      <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Save</Button>
      <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setValue(unit.ccEmails ?? ""); setError(null); }}>Cancel</Button>
      {error && <div className="text-xs text-rose-600">{error}</div>}
    </div>
  );
}

function RecipientsCard() {
  const queryClient = useQueryClient();
  const { data } = useQuery<OrgUnit[]>({
    queryKey: ["org-units"],
    queryFn: async () => (await api.get("/api/org-units")).data,
  });

  const [level, setLevel] = useState<string>("RBO");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [ccEmails, setCcEmails] = useState("");
  const [error, setError] = useState<string | null>(null);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["org-units"] });

  const add = useMutation({
    mutationFn: async () =>
      (await api.post("/api/org-units", { level, unitName: name, email, ccEmails: ccEmails || null })).data,
    onSuccess: () => {
      setName("");
      setEmail("");
      setCcEmails("");
      setError(null);
      invalidate();
    },
    onError: (err) => setError(apiErrorMessage(err, "Couldn't add that recipient.")),
  });

  const remove = useMutation({
    mutationFn: async (id: number) => api.delete(`/api/org-units/${id}`),
    onSuccess: invalidate,
  });

  return (
    <Card className="mt-6 max-w-4xl">
      <div className="flex items-center gap-3 border-b border-ink-100 px-6 py-5">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          <Users className="h-5 w-5" strokeWidth={2.25} />
        </div>
        <div>
          <div className="text-sm font-semibold text-ink-900">Report Recipients</div>
          <div className="text-xs text-ink-500">One email address (plus optional CC list) per RBO / LHO / AO / Corp Center that reports go to.</div>
        </div>
      </div>

      <div className="border-b border-ink-100 px-6 py-4">
        <div className="grid grid-cols-[110px_1fr_1fr_1fr_auto] gap-2">
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className="rounded-md border border-ink-300 px-2 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            {LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
          <input
            value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. RBO 5, Lucknow)"
            className="rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
          <input
            value={email} onChange={(e) => setEmail(e.target.value)} placeholder="To: email address"
            className="rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
          <input
            value={ccEmails} onChange={(e) => setCcEmails(e.target.value)} placeholder="CC (optional, comma-separated)"
            className="rounded-md border border-ink-300 px-2.5 py-1.5 text-sm focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
          <Button size="sm" onClick={() => add.mutate()} disabled={add.isPending || !name.trim() || !email.trim()}>
            <Plus className="h-3.5 w-3.5" strokeWidth={2.25} />
            Add
          </Button>
        </div>
        {error && <div className="mt-2 text-xs text-rose-600">{error}</div>}
      </div>

      {!data || data.length === 0 ? (
        <EmptyState title="No recipients yet" subtitle="Add the first email address above." icon={Users} />
      ) : (
        <div className="max-h-80 overflow-y-auto">
          <Table>
            <thead className="sticky top-0 bg-white"><tr><Th>Level</Th><Th>Name</Th><Th>To</Th><Th>CC</Th><Th /></tr></thead>
            <tbody>
              {data.map((u) => (
                <tr key={u.id}>
                  <Td><Badge tone="slate">{u.level}</Badge></Td>
                  <Td className="font-medium text-ink-900">{u.unitName}</Td>
                  <Td className="text-ink-500">{u.email}</Td>
                  <Td><CcCell unit={u} onSaved={invalidate} /></Td>
                  <Td className="text-right">
                    <button
                      onClick={() => remove.mutate(u.id)}
                      disabled={remove.isPending}
                      className="rounded-md p-1.5 text-ink-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-50"
                      title="Remove"
                    >
                      <Trash2 className="h-3.5 w-3.5" strokeWidth={2.25} />
                    </button>
                  </Td>
                </tr>
              ))}
            </tbody>
          </Table>
        </div>
      )}
    </Card>
  );
}

export default function Settings() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery<GmailStatus>({
    queryKey: ["gmail-status"],
    queryFn: async () => (await api.get("/api/gmail/status")).data,
  });

  const connect = useMutation({
    mutationFn: async () => api.post("/api/gmail/connect"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gmail-status"] }),
  });
  const disconnect = useMutation({
    mutationFn: async () => api.post("/api/gmail/disconnect"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["gmail-status"] }),
  });

  return (
    <div>
      <PageHeader title="Settings" subtitle="Connect the Gmail account that reads and sends every report." />

      <Card className="max-w-lg p-6">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
            <Mail className="h-5 w-5" strokeWidth={2.25} />
          </div>
          <div>
            <div className="text-sm font-semibold text-ink-900">Gmail Connection</div>
            <div className="text-xs text-ink-500">Used for sending reports and reading the calling sheet.</div>
          </div>
        </div>

        <div className="mt-5 rounded-lg border border-ink-100 bg-ink-50 p-4">
          {isLoading ? (
            <div className="flex items-center gap-2 text-sm text-ink-500">
              <Loader2 className="h-4 w-4 animate-spin" strokeWidth={2.25} />
              Checking connection...
            </div>
          ) : data?.connected ? (
            <div className="flex items-center gap-2 text-sm text-emerald-700">
              <CheckCircle2 className="h-4 w-4" strokeWidth={2.25} />
              Connected as <span className="font-medium">{data.email}</span>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-sm text-rose-600">
              <XCircle className="h-4 w-4" strokeWidth={2.25} />
              Not connected{data?.error ? ` — ${data.error}` : ""}
            </div>
          )}
        </div>

        {connect.isError && (
          <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700">
            {apiErrorMessage(connect.error, "Connection failed.")}
          </div>
        )}

        <div className="mt-5">
          {data?.connected ? (
            <Button variant="secondary" onClick={() => disconnect.mutate()} disabled={disconnect.isPending}>
              {disconnect.isPending ? "Disconnecting..." : "Disconnect Gmail"}
            </Button>
          ) : (
            <Button onClick={() => connect.mutate()} disabled={connect.isPending}>
              {connect.isPending ? "Opening browser..." : "Connect Gmail"}
            </Button>
          )}
        </div>
        {connect.isPending && (
          <p className="mt-2 text-xs text-ink-400">A browser window opens on this machine — sign in and approve access there.</p>
        )}
      </Card>

      <RecipientsCard />
    </div>
  );
}
