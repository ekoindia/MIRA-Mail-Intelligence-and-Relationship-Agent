import { useEffect, useState } from "react";
import { Clock } from "lucide-react";

/**
 * Live date/time display — the automation's fetch/send times (e.g. fetch
 * at 11:40, send at 12:00) fire relative to this same server clock, so
 * this is here as a visible reference for "what time the system thinks
 * it is right now."
 */
export default function LiveClock() {
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const date = now.toLocaleDateString(undefined, { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
  const time = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });

  return (
    <div className="flex items-center gap-2 rounded-lg border border-ink-200 bg-white px-3 py-1.5">
      <Clock className="h-3.5 w-3.5 text-brand-600" strokeWidth={2.25} />
      <div className="leading-tight">
        <div className="text-xs font-semibold tabular-nums text-ink-900">{time}</div>
        <div className="text-[10px] text-ink-400">{date}</div>
      </div>
    </div>
  );
}
