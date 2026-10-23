import type { ReactNode } from "react";
import { Check } from "lucide-react";

import { Toggle } from "../settings/ui";

/**
 * Детали страниц приложений (редизайн 24.10.2026). Замечание заказчика: «я этих флажков не
 * видел» — Bitrix24 выглядел подключённым, а отправку держал выключенной флажок посреди формы,
 * и с почтой то же самое. Теперь у каждой функции приложения своя карточка: название, что она
 * делает, состояние словами и главный переключатель справа в шапке, как в каталогах
 * интеграций. Под шапкой — пронумерованные шаги настройки в порядке, в котором их проходят.
 * Сверху страницы — сводка «что работает» со ссылками на карточки.
 */

/** Настройки приложения изменились — шапка страницы перечитывает метку «Подключено /
 * Выключено», иначе после переключателя она показывала бы прежнее состояние до перезагрузки. */
export const APP_STATUS_EVENT = "apps:status-changed";

export function notifyAppStatusChanged() {
  window.dispatchEvent(new Event(APP_STATUS_EVENT));
}

export type FeatureState = "on" | "paused" | "setup";

const STATE_DOT: Record<FeatureState, string> = {
  on: "bg-emerald-400",
  paused: "bg-amber-400",
  setup: "bg-zinc-500",
};

const STATE_LABEL: Record<FeatureState, string> = {
  on: "Работает",
  paused: "Выключено",
  setup: "Не настроено",
};

export function StateDot({ state }: { state: FeatureState }) {
  return (
    <span className={`h-2 w-2 shrink-0 rounded-full ${STATE_DOT[state]}`} />
  );
}

export interface SummaryItem {
  id: string;
  title: string;
  state: FeatureState;
  /** Что сделать или что происходит — одной фразой. */
  hint: string;
}

/** Сводка вверху страницы: каждая функция приложения, её состояние и подсказка. Нажатие
 * прокручивает к карточке функции. */
export function FeatureSummary({ items }: { items: SummaryItem[] }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {items.map((item) => (
        <a
          key={item.id}
          href={`#${item.id}`}
          onClick={(event) => {
            event.preventDefault();
            document
              .getElementById(item.id)
              ?.scrollIntoView({ behavior: "smooth", block: "start" });
          }}
          className="flex items-start gap-3 rounded-2xl border border-white/[0.08] bg-white/[0.03] px-4 py-3.5 transition-colors hover:border-white/[0.14] hover:bg-white/[0.05]"
        >
          <span className="mt-1.5">
            <StateDot state={item.state} />
          </span>
          <span className="min-w-0">
            <span className="flex flex-wrap items-baseline gap-x-2 text-sm font-medium text-zinc-100">
              {item.title}
              <span className="text-xs font-normal text-zinc-500">
                {STATE_LABEL[item.state]}
              </span>
            </span>
            <span className="mt-0.5 block text-xs leading-relaxed text-zinc-500">
              {item.hint}
            </span>
          </span>
        </a>
      ))}
    </div>
  );
}

/**
 * Карточка функции. `toggle` — главный переключатель: подпись «Включено/Выключено» стоит рядом
 * с ним текстом, а не только цветом. Пока функция не настроена, переключатель недоступен, и
 * `toggle.blockedHint` объясняет, какой шаг пройти. Без `toggle` — функция без включения
 * (выгрузка файла, журнал).
 */
export function FeatureCard({
  id,
  icon,
  title,
  description,
  state,
  toggle,
  warning,
  children,
}: {
  id: string;
  icon: ReactNode;
  title: string;
  description: ReactNode;
  state?: FeatureState;
  toggle?: {
    checked: boolean;
    onChange: (value: boolean) => void;
    disabled?: boolean;
    label: string;
    blockedHint?: string | null;
  };
  /** Предупреждение под шапкой: «портал боевой» и подобное — видно до того, как включат. */
  warning?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section
      id={id}
      className="scroll-mt-24 overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.03]"
    >
      <header className="flex flex-wrap items-start gap-4 px-5 py-5 sm:flex-nowrap">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-white/[0.05] text-zinc-300">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold text-zinc-100">{title}</h2>
            {state && (
              <span className="inline-flex items-center gap-1.5 rounded-full bg-white/[0.06] px-2.5 py-0.5 text-xs text-zinc-300">
                <StateDot state={state} />
                {STATE_LABEL[state]}
              </span>
            )}
          </div>
          <p className="mt-1 text-sm leading-relaxed text-zinc-400">
            {description}
          </p>
          {warning && (
            <div className="mt-2 text-xs leading-relaxed text-amber-300/90">
              {warning}
            </div>
          )}
        </div>
        {toggle && (
          <div className="flex shrink-0 flex-col items-end gap-1 sm:pl-4">
            <label className="flex cursor-pointer items-center gap-3">
              <span
                className={`text-sm font-medium ${toggle.checked ? "text-zinc-100" : "text-zinc-500"}`}
              >
                {toggle.checked ? "Включено" : "Выключено"}
              </span>
              <Toggle
                checked={toggle.checked}
                onChange={toggle.onChange}
                disabled={toggle.disabled}
                label={toggle.label}
              />
            </label>
            {toggle.blockedHint && (
              <span className="max-w-[220px] text-right text-xs text-zinc-500">
                {toggle.blockedHint}
              </span>
            )}
          </div>
        )}
      </header>
      {children && (
        <div className="divide-y divide-white/[0.06] border-t border-white/[0.06]">
          {children}
        </div>
      )}
    </section>
  );
}

/** Шаг настройки внутри карточки: номер (или галочка, когда шаг пройден), название, пояснение. */
export function Step({
  number,
  title,
  hint,
  done = false,
  children,
}: {
  number?: number;
  title: string;
  hint?: ReactNode;
  done?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex gap-4 px-5 py-5">
      {number !== undefined ? (
        <span
          className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold tabular-nums ${
            done
              ? "bg-emerald-500/15 text-emerald-400"
              : "bg-white/[0.08] text-zinc-300"
          }`}
        >
          {done ? <Check size={13} strokeWidth={3} /> : number}
        </span>
      ) : (
        <span className="w-6 shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-zinc-100">{title}</div>
        {hint && (
          <div className="mt-0.5 text-xs leading-relaxed text-zinc-500">
            {hint}
          </div>
        )}
        <div className="mt-3">{children}</div>
      </div>
    </div>
  );
}

/** Строка «название — пояснение — переключатель» для списков вроде триггеров писем. */
export function ToggleRow({
  title,
  hint,
  checked,
  onChange,
  children,
}: {
  title: string;
  hint?: ReactNode;
  checked: boolean;
  onChange: (value: boolean) => void;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 py-3 sm:flex-nowrap">
      <div className="min-w-0 flex-1">
        <div
          className={`text-sm ${checked ? "text-zinc-100" : "text-zinc-500"}`}
        >
          {title}
        </div>
        {hint && <div className="mt-0.5 text-xs text-zinc-500">{hint}</div>}
      </div>
      {children}
      <Toggle checked={checked} onChange={onChange} label={title} />
    </div>
  );
}
