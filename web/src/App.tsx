import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider, useAuth } from "./context/AuthContext";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Reports from "./pages/Reports";
import Templates from "./pages/Templates";
import Scheduler from "./pages/Scheduler";
import DeliveryLogs from "./pages/DeliveryLogs";
import AuditLogs from "./pages/AuditLogs";
import Suggestions from "./pages/Suggestions";
import SettingsPage from "./pages/Settings";
import ReportDetail from "./pages/ReportDetail";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter basename={import.meta.env.PROD ? "/mira" : undefined}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/report-detail" element={<ReportDetail />} />
            <Route
              element={
                <ProtectedRoute>
                  <Layout />
                </ProtectedRoute>
              }
            >
              <Route path="/" element={<Dashboard />} />
              <Route path="/reports" element={<Reports />} />
              <Route path="/templates" element={<Templates />} />
              <Route path="/scheduler" element={<Scheduler />} />
              <Route path="/delivery-logs" element={<DeliveryLogs />} />
              <Route path="/audit-logs" element={<AuditLogs />} />
              <Route path="/suggestions" element={<Suggestions />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
