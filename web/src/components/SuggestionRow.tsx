import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, apiErrorMessage } from "../lib/api";
import { Card, Button, Badge } from "./ui";

export interface SuggestionItem {
  id: number;
  category: string;
  title: string;
  description: string;
  severity: "info" | "warning" | "critical";
  canAutoFix: boolean;
  status: "pending" | "applied" | "dismissed" | "failed" | "resolved";
  detectedAt: string;
  resolvedAt: string | null;
  resolvedByUsername: string | null;
  resultDetail: string | null;
}

const SEVERITY_TONE: Record<string, "blue" | "amber" | "red"> = {
  info: "blue",
  warning: "amber",
  critical: "red",
};

const STATUS_TONE: Record<string, "slate" | "green" | "red"> = {
  pending: "slate",
  applied: "green",
  dismissed: "slate",
  failed: "red",
  resolved: "green",
};

const REASON: Record<string, string> = {
  data_dup_csp: "The Calling Sheet check found one CSP ID on more than one row.",
  data_sheet_unreadable: "MIRA tried to read the Calling Sheet and couldn't.",
  send_unrecorded: "Mail went out for a report today, but its run wasn't saved.",
  template_fk_null: "A digest has no email template linked, so it can't be drafted.",
  duplicate_draft_batch: "The same template was drafted more than once today.",
  incoming_new_category: "Many incoming mails share a subject no reply category covers.",
  incoming_low_confidence: "A reply category keeps matching incoming mail with low confidence.",
  incoming_ingest_error: "Some incoming mails were fetched but couldn't be processed.",
  incoming_learned_rule: "MIRA compared its acknowledgment drafts with the replies people actually sent.",
};

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: true,
  });
}

export function SuggestionRow({ suggestion, onMutated }: { suggestion: SuggestionItem; onMutated: () => void }) {
  const [error, setError] = useState<string | null>(null);

  const approve = useMutation({
    mutationFn: async () => (await api.post(`/api/suggestions/${suggestion.id}/approve`)).data,
    onSuccess: () => {
      setError(null);
      onMutated();
    },
    onError: (err) => setError(apiErrorMessage(err, "Couldn't apply this fix.")),
  });

  const dismiss = useMutation({
    mutationFn: async () => (await api.post(`/api/suggestions/${suggestion.id}/dismiss`)).data,
    onSuccess: () => {
      setError(null);
      onMutated();
    },
    onError: (err) => setError(apiErrorMessage(err, "Couldn't dismiss this.")),
  });

  const resolved = suggestion.status !== "pending";

  return (
    <Card className="mb-3">
      <div className="flex items-start justify-between gap-4 px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={SEVERITY_TONE[suggestion.severity] ?? "slate"}>{suggestion.severity}</Badge>
            <Badge tone={STATUS_TONE[suggestion.status] ?? "slate"}>{suggestion.status}</Badge>
            <span className="text-sm font-semibold text-ink-900">{suggestion.title}</span>
          </div>
          <p className="mt-1.5 text-xs text-ink-500">
            <span className="font-semibold text-ink-700">Detected:</span> {formatWhen(suggestion.detectedAt)}
            {resolved && suggestion.resolvedAt && (
              <> · <span className="font-semibold text-ink-700">{suggestion.status}:</span> {formatWhen(suggestion.resolvedAt)}</>
            )}
          </p>
          {REASON[suggestion.category] && (
            <p className="mt-1 text-xs text-ink-500">
              <span className="font-semibold text-ink-700">Reason:</span> {REASON[suggestion.category]}
            </p>
          )}
          <p className="mt-1.5 text-xs text-ink-600">{suggestion.description}</p>
          {resolved && suggestion.resultDetail && (
            <p className="mt-1.5 text-xs text-ink-400">
              {suggestion.resultDetail}
              {suggestion.resolvedByUsername && <> — by {suggestion.resolvedByUsername}</>}
            </p>
          )}
          {error && <div className="mt-2 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-700">{error}</div>}
        </div>

        {!resolved && (
          <div className="flex shrink-0 items-center gap-2">
            {suggestion.canAutoFix ? (
              <Button size="sm" onClick={() => approve.mutate()} disabled={approve.isPending || dismiss.isPending}>
                {approve.isPending ? "Applying…" : "Approve"}
              </Button>
            ) : (
              <span className="text-xs text-ink-400">Review manually</span>
            )}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => dismiss.mutate()}
              disabled={approve.isPending || dismiss.isPending}
            >
              {dismiss.isPending ? "…" : "Dismiss"}
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}
