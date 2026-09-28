import type { ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { motion } from "motion/react";

import { SPRING_SNAPPY } from "../../utils/motion";

/**
 * Общие детали страницы «Настройки» (редизайн 28.09.2026): заголовок вкладки с пояснением,
 * группы с подписью прописными буквами и карточки настроек «иконка — название с пояснением —
 * управление справа». Все разделы собираются из них, чтобы вкладки выглядели одной страницей,
 * а не набором блоков, каждый со своей шапкой.
 *
 * `id` у группы и карточки — адрес для поиска по настройкам: найденная настройка
 * прокручивается в центр и подсвечивается (см. `SETTINGS_INDEX` в pages/Settings.tsx).
 */

export function SettingsPanel({
  title,
  description,
  actions,
  children,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-3xl">
          <h2 className="text-xl font-semibold tracking-tight text-white">{title}</h2>
          {description && (
            <div className="mt-2 text-sm leading-relaxed text-zinc-400">{description}</div>
          )}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </div>
      <div className="space-y-10">{children}</div>
    </section>
  );
}

export function SettingsGroup({
  id,
  label,
  hint,
  actions,
  children,
}: {
  id?: string;
  label: string;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div id={id} data-setting-anchor={id ? "" : undefined} className="scroll-mt-28 rounded-2xl">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2 px-1">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-zinc-400">
            {label}
          </div>
          {hint && <div className="mt-1 text-xs leading-relaxed text-zinc-500">{hint}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

/** Карточка одной настройки. `active` подсвечивает её акцентом — включённый переключатель,
 * выбранный вариант. `children` — раскрытое содержимое под строкой (форма, список). */
export function SettingCard({
  id,
  icon,
  title,
  description,
  control,
  active = false,
  children,
}: {
  id?: string;
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  control?: ReactNode;
  active?: boolean;
  children?: ReactNode;
}) {
  return (
    <div
      id={id}
      data-setting-anchor={id ? "" : undefined}
      className={`scroll-mt-28 rounded-2xl border transition-colors ${
        active
          ? "border-indigo-500/25 bg-indigo-500/[0.08]"
          : "border-white/[0.08] bg-white/[0.03] hover:border-white/[0.12]"
      }`}
    >
      <div className="flex items-center gap-4 px-5 py-4">
        {icon && (
          <span
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${
              active ? "bg-indigo-500/15 text-indigo-300" : "bg-white/[0.05] text-zinc-400"
            }`}
          >
            {icon}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-zinc-100">{title}</div>
          {description && (
            <div className="mt-0.5 text-xs leading-relaxed text-zinc-500">{description}</div>
          )}
        </div>
        {control && <div className="flex shrink-0 items-center gap-2">{control}</div>}
      </div>
      {children && <div className="border-t border-white/[0.06] px-5 py-4">{children}</div>}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  disabled = false,
  label,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full p-0.5 transition-colors disabled:opacity-50 ${
        checked ? "justify-end bg-indigo-500" : "justify-start bg-white/15"
      }`}
    >
      <motion.span
        layout
        transition={SPRING_SNAPPY}
        className="h-5 w-5 rounded-full bg-snow shadow"
      />
    </button>
  );
}

export function StatusPill({
  tone,
  children,
  title,
}: {
  tone: "ok" | "bad" | "muted" | "accent";
  children: ReactNode;
  title?: string;
}) {
  const className = {
    ok: "bg-emerald-500/10 text-emerald-400",
    bad: "bg-red-500/10 text-red-400",
    muted: "bg-white/[0.06] text-zinc-400",
    accent: "bg-indigo-500/10 text-indigo-300",
  }[tone];
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ${className}`}
    >
      {children}
    </span>
  );
}

export function SettingsNotice({
  tone,
  children,
}: {
  tone: "error" | "success" | "info";
  children: ReactNode;
}) {
  const styles = {
    error: ["border-red-500/20 bg-red-500/10 text-red-400", AlertTriangle],
    success: ["border-emerald-500/20 bg-emerald-500/10 text-emerald-400", CheckCircle2],
    info: ["border-white/[0.08] bg-white/[0.03] text-zinc-400", Info],
  } as const;
  const [className, Icon] = styles[tone];
  return (
    <div className={`flex items-start gap-2.5 rounded-xl border px-4 py-3 text-sm ${className}`}>
      <Icon size={15} className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1 leading-relaxed">{children}</div>
    </div>
  );
}

export const settingsInputClass =
  "mt-1.5 w-full rounded-xl border border-white/10 bg-black/20 px-3.5 py-2.5 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";

export const primaryButtonClass =
  "inline-flex items-center justify-center gap-2 rounded-xl bg-indigo-500 px-4 py-2.5 text-sm font-medium text-snow transition-colors hover:bg-indigo-400 disabled:opacity-50";

export const secondaryButtonClass =
  "inline-flex items-center justify-center gap-1.5 rounded-xl border border-white/10 px-3 py-2 text-xs font-medium text-zinc-300 transition-colors hover:bg-white/5 disabled:opacity-50";

export const dangerButtonClass =
  "inline-flex items-center justify-center gap-1.5 rounded-xl border border-red-500/20 px-3 py-2 text-xs font-medium text-red-400 transition-colors hover:bg-red-500/10 disabled:opacity-50";
