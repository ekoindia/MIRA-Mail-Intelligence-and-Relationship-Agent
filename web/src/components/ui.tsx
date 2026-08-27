import type { ReactNode } from "react";
import { Loader2, Inbox } from "lucide-react";

export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="mb-7 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-ink-900">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ink-500">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-ink-200 bg-white shadow-sm ${className}`}>
      {children}
    </div>
  );
}

export function CardHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-ink-100 px-5 py-4">
      <div>
        <h2 className="text-sm font-semibold text-ink-900">{title}</h2>
        {subtitle && <p className="mt-0.5 text-xs text-ink-500">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function KpiCard({
  label, value, icon: Icon, tone = "brand",
}: { label: string; value: string | number; icon: React.ComponentType<{ className?: string; strokeWidth?: number }>; tone?: "brand" | "emerald" | "sky" | "rose" }) {
  const toneClasses: Record<string, string> = {
    brand: "bg-brand-50 text-brand-600",
    emerald: "bg-emerald-50 text-emerald-600",
    sky: "bg-sky-50 text-sky-600",
    rose: "bg-rose-50 text-rose-600",
  };
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium uppercase tracking-wide text-ink-500">{label}</span>
        <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${toneClasses[tone]}`}>
          <Icon className="h-4 w-4" strokeWidth={2.25} />
        </div>
      </div>
      <div className="mt-3 font-mono text-3xl font-semibold tracking-tight tabular-nums text-ink-900">{value}</div>
    </Card>
  );
}

// Status chip: soft fill + matching line + dot — the Eko Factory design
// system's "honest state, at a glance" pattern (good/warn/bad/info),
// applied over this app's existing slate/green/red/amber/blue tone names
// so no caller needs to change.
export function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "green" | "red" | "amber" | "blue" }) {
  const toneClasses: Record<string, { chip: string; dot: string }> = {
    slate: { chip: "bg-ink-100 text-ink-600 border-ink-200", dot: "bg-ink-400" },
    green: { chip: "bg-good-soft text-good-fg border-good-line", dot: "bg-good-fg" },
    red: { chip: "bg-bad-soft text-bad-fg border-bad-line", dot: "bg-bad-fg" },
    amber: { chip: "bg-warn-soft text-warn-fg border-warn-line", dot: "bg-warn-fg" },
    blue: { chip: "bg-info-soft text-info-fg border-info-line", dot: "bg-info-fg" },
  };
  const { chip, dot } = toneClasses[tone];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${chip}`}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
      {children}
    </span>
  );
}

export function Button({
  children, onClick, variant = "primary", size = "md", type = "button", disabled, className = "",
}: {
  children: ReactNode; onClick?: () => void; variant?: "primary" | "secondary" | "danger" | "ghost";
  size?: "sm" | "md"; type?: "button" | "submit"; disabled?: boolean; className?: string;
}) {
  const base = "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition-colors disabled:opacity-50 disabled:pointer-events-none";
  const sizes = { sm: "px-3 py-1.5 text-xs", md: "px-4 py-2 text-sm" };
  const variants: Record<string, string> = {
    primary: "bg-brand-600 text-white hover:bg-brand-700",
    secondary: "bg-white border border-ink-300 text-ink-700 hover:bg-ink-50",
    danger: "bg-rose-600 text-white hover:bg-rose-700",
    ghost: "text-ink-600 hover:bg-ink-100",
  };
  return (
    <button type={type} onClick={onClick} disabled={disabled} className={`${base} ${sizes[size]} ${variants[variant]} ${className}`}>
      {children}
    </button>
  );
}

export function EmptyState({ title, subtitle, icon: Icon = Inbox }: { title: string; subtitle?: string; icon?: React.ComponentType<{ className?: string; strokeWidth?: number }> }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <div className="flex h-11 w-11 items-center justify-center rounded-full bg-ink-100 text-ink-400">
        <Icon className="h-5 w-5" strokeWidth={1.75} />
      </div>
      <div className="text-sm font-medium text-ink-700">{title}</div>
      {subtitle && <div className="max-w-sm text-xs text-ink-500">{subtitle}</div>}
    </div>
  );
}

export function Spinner({ className = "h-5 w-5" }: { className?: string }) {
  return <Loader2 className={`animate-spin text-ink-400 ${className}`} strokeWidth={2} />;
}

export function LoadingBlock() {
  return (
    <div className="flex items-center justify-center py-16">
      <Spinner className="h-6 w-6" />
    </div>
  );
}

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">{children}</table>
    </div>
  );
}

export function Th({ children }: { children?: ReactNode }) {
  return <th className="border-b border-ink-100 px-5 py-3 text-xs font-semibold uppercase tracking-wide text-ink-500">{children}</th>;
}

export function Td({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <td className={`border-b border-ink-50 px-5 py-3 text-ink-700 ${className}`}>{children}</td>;
}

export function Toggle({ checked, onChange, disabled }: { checked: boolean; onChange: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={onChange}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${
        checked ? "bg-brand-600" : "bg-ink-300"
      }`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
          checked ? "translate-x-6" : "translate-x-1"
        }`}
      />
    </button>
  );
}
