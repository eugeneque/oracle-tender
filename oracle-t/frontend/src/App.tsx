import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider } from "./context/AuthContext";
import { AppLayout } from "./components/AppShell";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { WHATS_NEW_PATH } from "./components/nav/WhatsNew";
import { AccountPage } from "./pages/Account";
import { AiSettingsPage } from "./pages/AiSettings";
import { AnalyticsPage } from "./pages/Analytics";
import { AppDetailPage, AppsPage, LegacyIntegrationsRedirect } from "./pages/Apps";
import { CatalogPage } from "./pages/Catalog";
import { ChangelogPage } from "./pages/Changelog";
import { CompanyPage } from "./pages/Company";
import { HomePage } from "./pages/Home";
import { LoginPage } from "./pages/Login";
import { LogsPage } from "./pages/Logs";
import { SettingsPage } from "./pages/Settings";
import { TenderFullPage } from "./pages/TenderFull";
import { TendersPage } from "./pages/Tenders";
import { UsersPage } from "./pages/Users";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        {/* Каркас с меню — общий для всех страниц и не пересоздаётся при переходах. */}
        <Route
          element={
            <ProtectedRoute>
              <AppLayout />
            </ProtectedRoute>
          }
        >
          {/* Первой всегда открываются тендеры (28.09.2026); «Что нового?» — отдельный адрес. */}
          <Route path="/" element={<Navigate to="/tenders" replace />} />
          <Route path={WHATS_NEW_PATH} element={<HomePage />} />
          <Route path="/tenders" element={<TendersPage />} />
          <Route path="/tenders/:tenderId" element={<TenderFullPage />} />
          <Route path="/account" element={<AccountPage />} />
          <Route path="/analytics" element={<ProtectedRoute section="/analytics"><AnalyticsPage /></ProtectedRoute>} />
          <Route path="/catalog" element={<ProtectedRoute section="/catalog"><CatalogPage /></ProtectedRoute>} />
          <Route
            path="/users"
            element={
              <ProtectedRoute requireRole="admin">
                <UsersPage />
              </ProtectedRoute>
            }
          />
          <Route path="/company" element={<ProtectedRoute section="/company"><CompanyPage /></ProtectedRoute>} />
          {/* «Интеграции» разделены 23.10.2026: внешние сервисы — «Приложения», ИИ — свой раздел. */}
          <Route path="/apps" element={<ProtectedRoute section="/apps"><AppsPage /></ProtectedRoute>} />
          <Route path="/apps/:appKey" element={<ProtectedRoute section="/apps"><AppDetailPage /></ProtectedRoute>} />
          <Route
            path="/ai"
            element={
              <ProtectedRoute requireRole="admin">
                <AiSettingsPage />
              </ProtectedRoute>
            }
          />
          <Route path="/integrations" element={<LegacyIntegrationsRedirect />} />
          <Route path="/logs" element={<LogsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/changelog" element={<ChangelogPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/tenders" replace />} />
      </Routes>
    </AuthProvider>
  );
}
