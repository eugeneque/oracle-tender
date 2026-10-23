import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Eye, EyeOff } from "lucide-react";

import { ApiError } from "../api/client";
import { LogoMark } from "../components/Logo";
import { MirtekLogo } from "../components/MirtekLogo";
import { CHANGELOG } from "../content/changelog";
import { useAuth } from "../context/useAuth";

// Редизайн 23.10.2026: тёмный экран во всю ширину с дрейфующими серыми бликами, сеткой точек
// и крупным знаком совы на фоне, монохром, белая кнопка. Цвета заданы явными значениями, а
// не шкалой `zinc`: в светлой теме шкала зеркалится, а экран входа должен остаться тёмным.
const INPUT_CLASS =
  "h-11 w-full rounded-xl border border-snow/[0.08] bg-snow/[0.03] px-4 text-sm text-snow " +
  "placeholder-[#8e8e93] outline-none backdrop-blur-sm transition-colors " +
  "focus:border-snow/25 focus:bg-snow/[0.06]";

// Версия — по дате последней выкладки из журнала изменений («2026.10.23»): `version` в
// package.json не поднимается с 0.1.0, а журнал пополняется при каждой выкладке.
const APP_VERSION = CHANGELOG[0]?.date.replace(/-/g, ".") ?? "";

const rise = (step: number) => ({ animationDelay: `${step * 70}ms` });

export function LoginPage() {
  const { user, login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (user) {
    return <Navigate to="/tenders" replace />;
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login(username, password);
      navigate("/tenders");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось войти");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-[#161618]">
      <div className="login-glow login-glow--a" />
      <div className="login-glow login-glow--b" />
      <div className="login-grid" />
      <LogoMark
        size={380}
        className="pointer-events-none absolute -bottom-24 -right-32 hidden text-snow opacity-[0.03] lg:block"
      />

      <header className="relative flex items-center justify-between px-6 py-5 text-xs sm:px-10">
        <span className="font-medium tracking-tight text-snow">
          Sova <span className="text-[#8e8e93]">Scanner</span>
        </span>
        <span className="flex items-center gap-2 text-[#8e8e93]">
          <span className="h-1.5 w-1.5 rounded-full bg-snow/70 shadow-[0_0_8px_rgba(255,255,255,0.6)]" />
          Тендерная аналитика МИРТЕК
        </span>
      </header>

      <main className="relative flex flex-1 items-center justify-center px-4 py-10">
        <form onSubmit={handleSubmit} className="w-full max-w-[320px]">
          <div className="login-rise mb-8 flex justify-center" style={rise(0)}>
            <LogoMark
              size={34}
              className="text-snow drop-shadow-[0_0_24px_rgba(255,255,255,0.35)]"
            />
          </div>

          <div className="login-rise" style={rise(1)}>
            <h1 className="text-center text-[28px] font-normal tracking-tight text-snow">
              С возвращением!
            </h1>
            <p className="mb-9 mt-2 text-center text-sm text-[#8e8e93]">
              Для получения доступа обратитесь
              <br />
              к <span className="text-snow">администратору системы</span>
            </p>
          </div>

          <div className="login-rise space-y-2.5" style={rise(2)}>
            <input
              className={INPUT_CLASS}
              placeholder="Логин"
              aria-label="Логин"
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              required
            />
            <div className="relative">
              <input
                type={showPassword ? "text" : "password"}
                className={`${INPUT_CLASS} pr-11`}
                placeholder="Пароль"
                aria-label="Пароль"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}
                className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-[#636366] transition-colors hover:text-snow"
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          {error && (
            <p role="alert" className="mt-3 text-center text-sm text-[#ff6961]">
              {error}
            </p>
          )}

          <div className="login-rise" style={rise(3)}>
            <button
              type="submit"
              disabled={isSubmitting}
              className="mt-4 h-11 w-full rounded-xl bg-snow text-sm font-medium text-[#161618] shadow-[0_8px_30px_rgba(255,255,255,0.12)] transition-all hover:shadow-[0_8px_36px_rgba(255,255,255,0.22)] active:scale-[0.99] disabled:opacity-50"
            >
              {isSubmitting ? "Вход…" : "Войти"}
            </button>
            <p className="mt-4 hidden text-center text-xs text-[#636366] sm:block">
              Нажмите <kbd className="rounded border border-snow/10 px-1 font-sans text-[#8e8e93]">↵</kbd>,
              чтобы войти
            </p>
          </div>
        </form>
      </main>

      <footer className="login-rise relative px-6 pb-6 sm:px-10" style={rise(5)}>
        <div className="login-hairline mx-auto mb-5 max-w-3xl" />
        <div className="flex flex-col items-center gap-3">
          <MirtekLogo height={16} className="text-snow/50" />
          <p className="text-[11px] tabular-nums text-[#636366]">
            Версия {APP_VERSION} · © {new Date().getFullYear()}
          </p>
        </div>
      </footer>
    </div>
  );
}
