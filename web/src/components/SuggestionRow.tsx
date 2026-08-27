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
  status: "pending" | "applied" | "dismissed" | "failed";
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
};

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
