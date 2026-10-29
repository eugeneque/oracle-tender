import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "../context/useAuth";
import type { UserRole } from "../api/types";
import { canOpen } from "../utils/access";

export function ProtectedRoute({
  children,
  requireRole,
  section,
}: {
  children: ReactNode;
  requireRole?: UserRole;
  /** Адрес раздела: доступ по ролям из `utils/access.ts`. */
  section?: string;
}) {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return <div className="p-8 text-center text-slate-500">Загрузка…</div>;
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  if ((requireRole && user.role !== requireRole) || (section && !canOpen(user, section))) {
    return <Navigate to="/tenders" replace />;
  }
  return <>{children}</>;
}
