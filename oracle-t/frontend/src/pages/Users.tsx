import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Ban, CheckCircle2, Plus, Users as UsersIcon } from "lucide-react";

import { ApiError, api } from "../api/client";
import type { User, UserRole } from "../api/types";
import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";

function initials(fullName: string): string {
  const parts = fullName.trim().split(/\s+/);
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function UsersPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<UserRole>("user");
  const [isCreating, setIsCreating] = useState(false);

  const loadUsers = async () => {
    setIsLoading(true);
    try {
      setUsers(await api.get<User[]>("/users"));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить пользователей");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadUsers();
  }, []);

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setIsCreating(true);
    try {
      await api.post<User>("/users", { username, password, full_name: fullName, role });
      setUsername("");
      setPassword("");
      setFullName("");
      setRole("user");
      await loadUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось создать пользователя");
    } finally {
      setIsCreating(false);
    }
  };

  const toggleActive = async (user: User) => {
    setError(null);
    try {
      const action = user.is_active ? "block" : "unblock";
      await api.post<User>(`/users/${user.id}/${action}`);
      await loadUsers();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось изменить статус пользователя");
    }
  };

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-8 py-8">
        <PageHeader
          breadcrumb={["ORACLE-T", "Пользователи"]}
          title="Пользователи"
          icon={
            <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-indigo-500/10 text-indigo-400">
              <UsersIcon size={18} />
            </span>
          }
        />

        {error && (
          <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
            {error}
          </div>
        )}

        <form
          onSubmit={handleCreate}
          className="mb-6 grid grid-cols-1 gap-3 rounded-xl border border-white/[0.08] bg-white/[0.03] p-5 sm:grid-cols-2 lg:grid-cols-5"
        >
          <input
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
            placeholder="Логин"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          <input
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
            placeholder="ФИО"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            required
          />
          <input
            type="password"
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500"
            placeholder="Пароль"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
          />
          <select
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white outline-none focus:border-indigo-500"
            value={role}
            onChange={(e) => setRole(e.target.value as UserRole)}
          >
            <option className="bg-zinc-900" value="user">
              Пользователь
            </option>
            <option className="bg-zinc-900" value="admin">
              Администратор
            </option>
          </select>
          <button
            type="submit"
            disabled={isCreating}
            className="flex items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-3 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
          >
            <Plus size={16} />
            {isCreating ? "Создание…" : "Добавить"}
          </button>
        </form>

        <div className="overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-white/[0.08] text-zinc-500">
              <tr>
                <th className="px-5 py-3 font-medium">Пользователь</th>
                <th className="px-5 py-3 font-medium">Роль</th>
                <th className="px-5 py-3 font-medium">Статус</th>
                <th className="px-5 py-3 font-medium">Действие</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td className="px-5 py-4 text-zinc-500" colSpan={4}>
                    Загрузка…
                  </td>
                </tr>
              ) : (
                users.map((user) => (
                  <tr key={user.id} className="border-t border-white/[0.06]">
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-3">
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 text-xs font-semibold text-white">
                          {initials(user.full_name)}
                        </span>
                        <div className="min-w-0">
                          <div className="truncate text-zinc-100">{user.full_name}</div>
                          <div className="truncate text-xs text-zinc-500">
                            {user.username}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-3 text-zinc-400">
                      {user.role === "admin" ? "Администратор" : "Пользователь"}
                    </td>
                    <td className="px-5 py-3">
                      <span
                        className={
                          user.is_active
                            ? "rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-400"
                            : "rounded-full bg-red-500/10 px-2.5 py-1 text-xs font-medium text-red-400"
                        }
                      >
                        {user.is_active ? "активен" : "заблокирован"}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      <button
                        onClick={() => toggleActive(user)}
                        className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5"
                      >
                        {user.is_active ? (
                          <>
                            <Ban size={13} />
                            Заблокировать
                          </>
                        ) : (
                          <>
                            <CheckCircle2 size={13} />
                            Разблокировать
                          </>
                        )}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </AppShell>
  );
}
