import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "../context/useAuth";
import type { UserRole } from "../api/types";

export function ProtectedRoute({
  children,
  requireRole,
}: {
  children: ReactNode;
  requireRole?: UserRole;
}) {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return <div className="p-8 text-center text-slate-500">Загрузка…</div>;
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  if (requireRole && user.role !== requireRole) {
    return <Navigate to="/" replace />;
  }
  return <>{children}</>;
}
