import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Sparkles, Inbox, ArrowUpRight } from "lucide-react";
import { api } from "../lib/api";
import { PageHeader, Card, Button, EmptyState, LoadingBlock, Badge } from "../components/ui";
import { SuggestionRow, type SuggestionItem } from "../components/SuggestionRow";

function SuggestionSection({
  icon: Icon, title, subtitle, suggestions, onMutated,
}: {
  icon: React.ComponentType<{ className?: string; strokeWidth?: number }>;
  title: string;
  subtitle: string;
  suggestions: SuggestionItem[];
  onMutated: () => void;
}) {
  const openCount = suggestions.filter((s) => s.status === "pending").length;

  return (
    <div className="mb-8">
      <div className="mb-3 flex items-center gap-2">
        <Icon className="h-4 w-4 text-brand-600" strokeWidth={2.25} />
        <h2 className="text-sm font-semibold text-ink-900">{title}</h2>
        {openCount > 0 && <Badge tone="amber">{openCount} open</Badge>}
      </div>
      <p className="mb-3 text-xs text-ink-500">{subtitle}</p>

      {suggestions.length === 0 ? (
        <Card>
          <EmptyState icon={Icon} title="Nothing here right now" subtitle="Rescan to check again." />
        </Card>
      ) : (
        <div>
          {suggestions.map((s) => (
            <SuggestionRow key={s.id} suggestion={s} onMutated={onMutated} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function Suggestions() {
  const queryClient = useQueryClient();
  const { data: suggestions, isLoading } = useQuery<SuggestionItem[]>({
    queryKey: ["suggestions"],
    queryFn: async () => (await api.get("/api/suggestions")).data,
  });

  const rescan = useMutation({
    mutationFn: async () => (await api.post("/api/suggestions/scan")).data,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["suggestions"] }),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["suggestions"] });
    queryClient.invalidateQueries({ queryKey: ["incoming-summary"] });
    queryClient.invalidateQueries({ queryKey: ["incoming-patterns"] });
    queryClient.invalidateQueries({ queryKey: ["incoming-reply-match-summary"] });
  };

  const outgoing = (suggestions ?? []).filter((s) => !s.category.startsWith("incoming_"));
  const incoming = (suggestions ?? []).filter((s) => s.category.startsWith("incoming_"));

  return (
    <div>
      <PageHeader
        title="Suggestions"
        subtitle="The app watches its own templates, schedules, delivery history, and incoming-mail patterns and flags what needs attention. Approve applies a bounded, pre-defined fix — nothing here ever drafts or sends an email on its own."
        action={
          <Button variant="secondary" onClick={() => rescan.mutate()} disabled={rescan.isPending}>
            <RefreshCw className={`h-3.5 w-3.5 ${rescan.isPending ? "animate-spin" : ""}`} strokeWidth={2.25} />
            {rescan.isPending ? "Scanning…" : "Rescan now"}
          </Button>
        }
      />

      {isLoading ? (
        <LoadingBlock />
      ) : !suggestions || suggestions.length === 0 ? (
        <Card>
          <EmptyState
            icon={Sparkles}
            title="Nothing needs attention right now"
            subtitle="Suggestions are scanned automatically every 30 minutes, or click Rescan now to check immediately."
          />
        </Card>
      ) : (
        <>
          <SuggestionSection
            icon={ArrowUpRight}
            title="Outgoing Reports"
            subtitle="Broken template links, duplicate draft batches, and other issues in the outbound report pipeline."
            suggestions={outgoing}
            onMutated={invalidate}
          />
          <SuggestionSection
            icon={Inbox}
            title="Incoming Mail"
            subtitle="Reply-category gaps, low-confidence matches, and ingest errors spotted in the connected inbox. Approve creates or edits a category on the Templates → Incoming page; nothing is ever drafted or sent."
            suggestions={incoming}
            onMutated={invalidate}
          />
        </>
      )}
    </div>
  );
}
