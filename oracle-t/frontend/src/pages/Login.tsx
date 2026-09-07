import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Lock, User as UserIcon } from "lucide-react";

import { ApiError } from "../api/client";
import { Logo } from "../components/Logo";
import { useAuth } from "../context/useAuth";

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (user) {
    return <Navigate to="/" replace />;
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login(username, password);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось войти");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-zinc-950 px-4">
      <div className="pointer-events-none absolute left-1/2 top-0 h-[480px] w-[720px] -translate-x-1/2 -translate-y-1/3 rounded-full bg-indigo-600/20 blur-[120px]" />

      <form
        onSubmit={handleSubmit}
        className="relative w-full max-w-sm rounded-2xl border border-white/10 bg-zinc-900/80 p-8 shadow-2xl backdrop-blur"
      >
        <div className="mb-8 flex justify-center">
          <Logo size={34} />
        </div>

        <h1 className="mb-1 text-center text-lg font-semibold text-white">
          Вход в систему
        </h1>
        <p className="mb-7 text-center text-sm text-zinc-500">
          Автоматизированный сбор и анализ тендерной информации
        </p>

        <label className="mb-3 block text-sm">
          <span className="mb-1.5 block text-zinc-400">Логин</span>
          <div className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5 focus-within:border-indigo-500">
            <UserIcon size={16} className="text-zinc-500" />
            <input
              className="w-full bg-transparent text-white placeholder-zinc-600 outline-none"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              required
            />
          </div>
        </label>

        <label className="mb-5 block text-sm">
          <span className="mb-1.5 block text-zinc-400">Пароль</span>
          <div className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2.5 focus-within:border-indigo-500">
            <Lock size={16} className="text-zinc-500" />
            <input
              type="password"
              className="w-full bg-transparent text-white placeholder-zinc-600 outline-none"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
        </label>

        {error && (
          <p className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-sm text-red-400">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={isSubmitting}
          className="w-full rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-3 py-2.5 text-sm font-medium text-white shadow-lg shadow-indigo-500/20 transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {isSubmitting ? "Вход…" : "Войти"}
        </button>
      </form>
    </div>
  );
}
