import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Ban, CheckCircle2, Plus, Search, Trash2, Users as UsersIcon, X } from "lucide-react";

import { ApiError, api } from "../api/client";
import type { User, UserRole } from "../api/types";
import { AppShell } from "../components/AppShell";
import { PageHeader } from "../components/PageHeader";
import { UserAvatar } from "../components/UserAvatar";
import { useAuth } from "../context/useAuth";

const ROLE_LABELS: Record<UserRole, string> = {
  admin: "Администратор",
  user: "Пользователь",
};

const INPUT_CLASS =
  "rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white placeholder-zinc-600 outline-none focus:border-indigo-500";

/** Подтверждение удаления (замечание 28.10.2026): удаление необратимо и освобождает логин,
 * поэтому не `window.confirm`, а окно с объяснением, что останется в системе. */
function DeleteUserDialog({
  user,
  isDeleting,
  onCancel,
  onConfirm,
}: {
  user: User;
  isDeleting: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isDeleting) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isDeleting, onCancel]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      onClick={() => !isDeleting && onCancel()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-user-title"
        className="w-full max-w-md rounded-xl border border-white/[0.08] bg-zinc-900 p-6 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <h2 id="delete-user-title" className="text-base font-semibold text-zinc-100">
            Удалить пользователя?
          </h2>
          <button
            type="button"
            onClick={onCancel}
            disabled={isDeleting}
            className="rounded-md p-1 text-zinc-500 hover:bg-white/5 hover:text-zinc-300"
            aria-label="Закрыть"
          >
            <X size={16} />
          </button>
        </div>
        <div className="mb-4 flex items-center gap-3 rounded-lg border border-white/[0.06] bg-white/[0.02] p-3">
          <UserAvatar user={user} size="sm" />
          <div className="min-w-0">
            <div className="truncate text-sm text-zinc-100">{user.full_name}</div>
            <div className="truncate text-xs text-zinc-500">{user.username}</div>
          </div>
        </div>
        <ul className="mb-6 space-y-1.5 text-sm text-zinc-400">
          <li>• Учётная запись и логин удаляются, войти под ним больше нельзя.</li>
          <li>• Логин освободится — его можно будет выдать заново.</li>
          <li>
            • Журнал, история тендеров и личные профили отбора останутся, но без подписи автора.
          </li>
          <li>• Действие нельзя отменить. Чтобы просто закрыть доступ — заблокируйте.</li>
        </ul>
        <div className="flex justify-end gap-2 whitespace-nowrap">
          <button
            type="button"
            onClick={onCancel}
            disabled={isDeleting}
            className="rounded-lg border border-white/10 px-3 py-2 text-sm text-zinc-300 hover:bg-white/5"
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={isDeleting}
            className="flex items-center gap-1.5 rounded-lg bg-red-500 px-3 py-2 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50"
          >
            <Trash2 size={14} />
            {isDeleting ? "Удаление…" : "Удалить навсегда"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function UsersPage() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  // id строки, по которой идёт запрос, — чтобы не нажать дважды и показать, что идёт работа.
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<User | null>(null);

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

  // Последнего активного администратора нельзя понизить, заблокировать или удалить — иначе
  // доступ к этому разделу вернёт только `reset_admin.py` на сервере. Бэкенд проверяет то же.
  const activeAdmins = users.filter((u) => u.role === "admin" && u.is_active).length;
  const isLastAdmin = (user: User) => user.role === "admin" && user.is_active && activeAdmins <= 1;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return users;
    return users.filter(
      (u) => u.full_name.toLowerCase().includes(q) || u.username.toLowerCase().includes(q),
    );
  }, [users, query]);

  const replaceUser = (updated: User) =>
    setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setIsCreating(true);
    try {
      const created = await api.post<User>("/users", {
        username,
        password,
        full_name: fullName,
        role,
      });
      setUsers((prev) => [...prev, created]);
      setUsername("");
      setPassword("");
      setFullName("");
      setRole("user");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось создать пользователя");
    } finally {
      setIsCreating(false);
    }
  };

  const changeRole = async (user: User, nextRole: UserRole) => {
    if (nextRole === user.role) return;
    setError(null);
    setBusyId(user.id);
    try {
      replaceUser(await api.patch<User>(`/users/${user.id}`, { role: nextRole }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось изменить роль");
    } finally {
      setBusyId(null);
    }
  };

  const toggleActive = async (user: User) => {
    setError(null);
    setBusyId(user.id);
    try {
      const action = user.is_active ? "block" : "unblock";
      replaceUser(await api.post<User>(`/users/${user.id}/${action}`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось изменить статус пользователя");
    } finally {
      setBusyId(null);
    }
  };

  const confirmDelete = async () => {
    if (!toDelete) return;
    setError(null);
    setBusyId(toDelete.id);
    try {
      await api.delete<void>(`/users/${toDelete.id}`);
      setUsers((prev) => prev.filter((u) => u.id !== toDelete.id));
      setToDelete(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить пользователя");
      setToDelete(null);
    } finally {
      setBusyId(null);
    }
  };

  const blockedCount = users.filter((u) => !u.is_active).length;

  return (
    <AppShell>
      <div className="mx-auto max-w-5xl px-8 py-8">
        <PageHeader
          breadcrumb={["Sova", "Пользователи"]}
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
          className="mb-6 rounded-xl border border-white/[0.08] bg-white/[0.03] p-5"
        >
          <div className="mb-3 text-sm font-medium text-zinc-200">Новый пользователь</div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <input
              className={INPUT_CLASS}
              placeholder="Логин"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              minLength={3}
              required
            />
            <input
              className={INPUT_CLASS}
              placeholder="ФИО"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              required
            />
            <input
              type="password"
              className={INPUT_CLASS}
              placeholder="Пароль (от 8 символов)"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              minLength={8}
              required
            />
            <select
              className={INPUT_CLASS}
              value={role}
              onChange={(e) => setRole(e.target.value as UserRole)}
            >
              <option className="bg-zinc-900" value="user">
                {ROLE_LABELS.user}
              </option>
              <option className="bg-zinc-900" value="admin">
                {ROLE_LABELS.admin}
              </option>
            </select>
            <button
              type="submit"
              disabled={isCreating}
              className="flex items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-indigo-500 to-indigo-400 px-3 py-2 text-sm font-medium text-snow transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              <Plus size={16} />
              {isCreating ? "Создание…" : "Добавить"}
            </button>
          </div>
        </form>

        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm text-zinc-500">
            {users.length} всего · {activeAdmins} адм. · {blockedCount} заблок.
          </div>
          <label className="relative">
            <Search
              size={14}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500"
            />
            <input
              className={`${INPUT_CLASS} w-64 pl-8`}
              placeholder="Поиск по имени или логину"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
        </div>

        <div className="overflow-x-auto rounded-xl border border-white/[0.08] bg-white/[0.03]">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-white/[0.08] text-zinc-500">
              <tr>
                <th className="px-5 py-3 font-medium">Пользователь</th>
                <th className="px-5 py-3 font-medium">Роль</th>
                <th className="px-5 py-3 font-medium">Статус</th>
                <th className="px-5 py-3 text-right font-medium">Действия</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td className="px-5 py-4 text-zinc-500" colSpan={4}>
                    Загрузка…
                  </td>
                </tr>
              ) : filtered.length === 0 ? (
                <tr>
                  <td className="px-5 py-4 text-zinc-500" colSpan={4}>
                    {query ? "Никого не нашлось" : "Пользователей нет"}
                  </td>
                </tr>
              ) : (
                filtered.map((user) => {
                  const isSelf = user.id === me?.id;
                  const isBusy = busyId === user.id;
                  const lastAdmin = isLastAdmin(user);
                  const roleLocked = isSelf || lastAdmin;
                  const protectedHint = isSelf
                    ? "Это ваша учётная запись"
                    : lastAdmin
                      ? "Последний активный администратор — сначала назначьте другого"
                      : undefined;
                  return (
                    <tr key={user.id} className="border-t border-white/[0.06]">
                      <td className="px-5 py-3">
                        <div className="flex items-center gap-3">
                          <UserAvatar user={user} size="sm" />
                          <div className="min-w-0">
                            <div className="truncate text-zinc-100">
                              {user.full_name}
                              {isSelf && <span className="ml-2 text-xs text-zinc-500">вы</span>}
                            </div>
                            <div className="truncate text-xs text-zinc-500">{user.username}</div>
                          </div>
                        </div>
                      </td>
                      <td className="px-5 py-3">
                        <select
                          className={`${INPUT_CLASS} py-1.5 disabled:cursor-not-allowed disabled:opacity-60`}
                          value={user.role}
                          disabled={isBusy || roleLocked}
                          title={roleLocked ? protectedHint : "Изменить роль"}
                          onChange={(e) => void changeRole(user, e.target.value as UserRole)}
                        >
                          <option className="bg-zinc-900" value="user">
                            {ROLE_LABELS.user}
                          </option>
                          <option className="bg-zinc-900" value="admin">
                            {ROLE_LABELS.admin}
                          </option>
                        </select>
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
                        <div className="flex justify-end gap-2 whitespace-nowrap">
                          <button
                            onClick={() => void toggleActive(user)}
                            disabled={isBusy || isSelf || lastAdmin}
                            title={protectedHint}
                            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-40"
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
                          <button
                            onClick={() => setToDelete(user)}
                            disabled={isBusy || isSelf || lastAdmin}
                            title={protectedHint ?? "Удалить из системы вместе с логином"}
                            className="flex items-center gap-1.5 rounded-lg border border-red-500/20 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-500/10 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            <Trash2 size={13} />
                            Удалить
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {toDelete && (
        <DeleteUserDialog
          user={toDelete}
          isDeleting={busyId === toDelete.id}
          onCancel={() => setToDelete(null)}
          onConfirm={() => void confirmDelete()}
        />
      )}
    </AppShell>
  );
}
