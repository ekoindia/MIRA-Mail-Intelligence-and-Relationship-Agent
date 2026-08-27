import axios from "axios";

// Production build (rack server) talks to the backend same-origin — nginx
// serves this build and reverse-proxies /api/ to the backend on the SAME
// dedicated port (see the server's nginx site config), so a relative,
// empty base resolves correctly. Local dev keeps hitting the backend
// directly on :8000, unchanged.
export const API_BASE = import.meta.env.PROD ? "" : "http://127.0.0.1:8000";

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
