import { NavLink, Outlet, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  FileStack,
  Mail,
  CalendarClock,
  Send,
  History,
  Sparkles,
  Settings as SettingsIcon,
  LogOut,
} from "lucide-react";
import { useAuth } from "../context/AuthContext";
import EkoLogo from "./EkoLogo";
import LiveClock from "./LiveClock";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, end: true },
  { to: "/reports", label: "Reports", icon: FileStack },
  { to: "/templates", label: "Templates", icon: Mail },
  { to: "/scheduler", label: "Scheduler", icon: CalendarClock },
  { to: "/delivery-logs", label: "Delivery Logs", icon: Send },
  { to: "/audit-logs", label: "Audit Logs", icon: History },
  { to: "/suggestions", label: "Suggestions", icon: Sparkles },
  { to: "/settings", label: "Settings", icon: SettingsIcon },
];

export default function Layout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login");
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-ink-50">
      <aside className="flex w-64 shrink-0 flex-col border-r border-ink-200 bg-white">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <EkoLogo className="h-9 w-auto rounded" />
          <div>
            <div className="text-sm font-semibold leading-tight text-ink-900">MIRA</div>
            <div className="text-xs leading-tight text-ink-400">Eko Kiosk</div>
          </div>
        </div>

        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 py-2">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-brand-50 text-brand-700"
                    : "text-ink-600 hover:bg-ink-100 hover:text-ink-900"
                }`
              }
            >
              <Icon className="h-[18px] w-[18px]" strokeWidth={2} />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-ink-200 p-3">
          <div className="flex items-center gap-3 rounded-lg px-3 py-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-full bg-ink-800 text-xs font-semibold text-white">
              {user?.username?.slice(0, 2).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-ink-900">{user?.username}</div>
              <div className="truncate text-xs text-ink-400">{user?.role}</div>
            </div>
            <button
              onClick={handleLogout}
              className="rounded-md p-1.5 text-ink-400 hover:bg-ink-100 hover:text-ink-700"
              title="Log out"
            >
              <LogOut className="h-4 w-4" strokeWidth={2} />
            </button>
          </div>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <div className="flex justify-end px-8 pt-5">
          <LiveClock />
        </div>
        <div className="mx-auto max-w-7xl px-8 pb-8 pt-3">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
