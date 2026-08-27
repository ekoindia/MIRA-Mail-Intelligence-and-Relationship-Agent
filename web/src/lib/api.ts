import axios from "axios";

// Production build (rack server, served under /mira/ by nginx) talks to the
// backend same-origin via the /mira/api/ proxy path — see vite.config.ts's
// build-only base and the nginx location block that pairs with it. Local
// dev keeps hitting the backend directly on :8000, unchanged.
export const API_BASE = import.meta.env.PROD ? "/mira" : "http://127.0.0.1:8000";

export const api = axios.create({ baseURL: API_BASE });

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem("token");
      localStorage.removeItem("user");
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }
    return Promise.reject(err);
  },
);

export function apiErrorMessage(err: unknown, fallback = "Something went wrong."): string {
  if (axios.isAxiosError(err)) {
    return (err.response?.data as { detail?: string } | undefined)?.detail ?? fallback;
  }
  return fallback;
}
