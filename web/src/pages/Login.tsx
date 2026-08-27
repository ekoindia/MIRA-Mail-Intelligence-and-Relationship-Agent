import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { LoaderCircle } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { apiErrorMessage } from "../lib/api";
import EkoLogo from "../components/EkoLogo";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
      navigate("/");
    } catch (err) {
      setError(apiErrorMessage(err, "Invalid username or password."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-ink-950 px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3">
          <EkoLogo className="h-14 w-auto rounded-lg shadow-lg" />
          <div className="text-center">
            <div className="text-lg font-semibold text-white">MIRA</div>
            <div className="text-sm text-ink-400">Eko Kiosk</div>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="rounded-2xl border border-ink-800 bg-ink-900 p-7 shadow-xl">
          <div className="space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-400">Username</label>
              <input
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full rounded-lg border border-ink-700 bg-ink-950 px-3 py-2.5 text-sm text-white placeholder:text-ink-500 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                placeholder="Enter your username"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-400">Password</label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-ink-700 bg-ink-950 px-3 py-2.5 text-sm text-white placeholder:text-ink-500 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
                placeholder="Enter your password"
              />
            </div>
          </div>

          {error && (
            <div className="mt-4 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-rose-300">{error}</div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="mt-6 flex w-full items-center justify-center gap-2 rounded-lg bg-brand-600 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-brand-500 disabled:opacity-60"
          >
            {loading && <LoaderCircle className="h-4 w-4 animate-spin" strokeWidth={2.5} />}
            Sign in
          </button>
        </form>
      </div>
    </div>
  );
}
