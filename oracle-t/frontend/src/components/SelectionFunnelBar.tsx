import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { CircleHelp, Info, Loader2, Star } from "lucide-react";

import type { SelectionFunnel } from "../api/types";
import { plural } from "../utils/format";

/**
 * Счётчик «N закупок» рядом с заголовком и детализация отбора во всплывающей карточке
 * (14.10.2026). С 05.10.2026 над списком стояла развёрнутая воронка «собрано → профили →
 * модель → фильтры → в списке» — пять чипов с числами и минусами на всю ширину. Она
 * объясняла, ЧЕМ скрыты закупки, но кричала тысячами, которые в работе не нужны, и
 * отодвигала список. Теперь на виду — только сколько показано; откуда это число и что
 * снять, чтобы увидеть больше, — по наведению на «ⓘ», с теми же кнопками снять слой.
 */

const fmt = (value: number) => value.toLocaleString("ru-RU");

function Step({
  label,
  hint,
  value,
  delta,
  action,
}: {
  label: string;
  hint?: string;
  value: number;
  delta?: number;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 py-1.5">
      <div className="min-w-0 flex-1">
        <div className="text-zinc-200">{label}</div>
        {hint && <div className="text-[11px] leading-snug text-zinc-500">{hint}</div>}
        {action && <div className="mt-0.5">{action}</div>}
      </div>
      <div className="shrink-0 text-right tabular-nums">
        <div className="font-medium text-zinc-100">{fmt(value)}</div>
        {delta !== undefined && delta > 0 && (
          <div className="text-[11px] text-rose-300/80">−{fmt(delta)}</div>
        )}
      </div>
    </div>
  );
}

function LinkButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="text-[11px] text-indigo-400 hover:text-indigo-300">
      {children}
    </button>
  );
}

export function SelectionCounter({
  funnel,
  isLoading,
  feedLabel,
  profilesLabel,
  favouritesOnly,
  favouritesTotal,
  onExitFavourites,
  onClearProfiles,
  onDisableAi,
  onResetFilters,
  onOpenGuide,
  onEditProfiles,
}: {
  funnel: SelectionFunnel | null;
  isLoading: boolean;
  feedLabel: string;
  /** Подпись слоя профилей: «Общие профили», «Щитовые», «Без профилей». */
  profilesLabel: string;
  /** Раздел «Избранное»: воронка к нему не относится — показываем только число. */
  favouritesOnly: boolean;
  favouritesTotal: number;
  onExitFavourites: () => void;
  onClearProfiles?: () => void;
  onDisableAi?: () => void;
  onResetFilters?: () => void;
  onOpenGuide: () => void;
  onEditProfiles: () => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const closeTimer = useRef<number | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // Карточка открывается наведением, но в ней есть кнопки — закрываем с задержкой, чтобы
  // курсор успел доехать от «ⓘ» до карточки и не схлопнул её по дороге.
  const open = () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    setIsOpen(true);
  };
  const scheduleClose = () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setIsOpen(false), 180);
  };
  useEffect(
    () => () => {
      if (closeTimer.current) window.clearTimeout(closeTimer.current);
    },
    [],
  );
  useEffect(() => {
    if (!isOpen) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setIsOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [isOpen]);
  // Кнопка в карточке меняет выдачу — карточку закрываем, иначе она висит поверх списка.
  const act = (fn?: () => void) => () => {
    setIsOpen(false);
    fn?.();
  };

  const shown = favouritesOnly ? favouritesTotal : funnel?.shown ?? null;

  const counter = (
    <span className="flex items-baseline gap-1.5 text-sm text-zinc-400">
      {favouritesOnly && <Star size={12} className="self-center text-amber-400" fill="currentColor" />}
      {shown === null ? (
        <span className="text-zinc-600">считаем…</span>
      ) : (
        <>
          <span className="font-medium tabular-nums text-zinc-100">{fmt(shown)}</span>
          {plural(shown, "закупка", "закупки", "закупок")}
          {favouritesOnly && " в избранном"}
        </>
      )}
    </span>
  );

  let card: ReactNode;
  if (favouritesOnly) {
    card = (
      <>
        <div className="text-zinc-300">
          Раздел «Избранное»: все отмеченные звёздочкой закупки. Срок подачи, профили и
          проверка моделью здесь не учитываются.
        </div>
        <div className="mt-2">
          <LinkButton onClick={act(onExitFavourites)}>Ко всем закупкам</LinkButton>
        </div>
      </>
    );
  } else if (!funnel) {
    card = (
      <div className="flex items-center gap-1.5 text-zinc-500">
        <Loader2 size={12} className="animate-spin" />
        Считаем…
      </div>
    );
  } else {
    const profilesCut = funnel.collected - funnel.after_profiles;
    const aiCut = funnel.after_profiles - funnel.after_ai;
    const filtersCut = funnel.after_ai - funnel.shown;
    const hasProfiles = funnel.profiles.length > 0;
    card = (
      <>
        <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
          Как отобраны закупки
        </div>
        <div className="divide-y divide-white/[0.06]">
          <Step label="Собрано" hint={feedLabel} value={funnel.collected} />
          <Step
            label={profilesLabel}
            hint="Ключевые слова и коды ОКПД2 профилей отбора"
            value={funnel.after_profiles}
            delta={profilesCut}
            action={
              <span className="flex gap-3">
                {hasProfiles && onClearProfiles && (
                  <LinkButton onClick={act(onClearProfiles)}>Показать без профилей</LinkButton>
                )}
                <LinkButton onClick={act(onEditProfiles)}>Изменить профили…</LinkButton>
              </span>
            }
          />
          <Step
            label={funnel.ai_enabled ? "Проверка моделью" : "Проверка моделью выключена"}
            hint={funnel.ai_enabled ? "Скрыты закупки, которые ИИ счёл нерелевантными" : undefined}
            value={funnel.after_ai}
            delta={aiCut}
            action={
              funnel.ai_enabled && aiCut > 0 && onDisableAi ? (
                <LinkButton onClick={act(onDisableAi)}>Показать отклонённые моделью</LinkButton>
              ) : undefined
            }
          />
          <Step
            label="Фильтры"
            hint="Срок подачи, площадки, цена, регионы, метки"
            value={funnel.shown}
            delta={filtersCut}
            action={
              filtersCut > 0 && onResetFilters ? (
                <LinkButton onClick={act(onResetFilters)}>Сбросить фильтры</LinkButton>
              ) : undefined
            }
          />
        </div>

        {hasProfiles && (
          <div className="mt-2 border-t border-white/[0.06] pt-2">
            <div className="mb-1 text-[11px] text-zinc-500">
              {funnel.profiles.length > 1
                ? `Что отобрал каждый профиль (на одной площадке — ${
                    funnel.profiles_mode === "any" ? "подошёл любой" : "подошли все"
                  }):`
                : "Что отобрал профиль:"}
            </div>
            <div className="max-h-40 space-y-1 overflow-y-auto">
              {funnel.profiles.map((item) => (
                <div key={item.profile_id} className="flex items-baseline gap-2">
                  <span
                    className="min-w-0 flex-1 truncate text-zinc-300"
                    title={item.sources.length === 0 ? "все площадки" : item.sources.join(", ")}
                  >
                    {item.name}
                    <span className="text-zinc-600"> · {item.is_default ? "общий" : "личный"}</span>
                  </span>
                  <span className="shrink-0 tabular-nums text-zinc-400">
                    {item.in_scope === 0 ? (
                      <span className="text-amber-300">не действует здесь</span>
                    ) : (
                      <>
                        <b className="font-medium text-zinc-200">{fmt(item.matched)}</b> из{" "}
                        {fmt(item.in_scope)}
                      </>
                    )}
                  </span>
                </div>
              ))}
            </div>
            {funnel.untouched_by_profiles > 0 && (
              <div className="mt-1.5 text-[11px] leading-snug text-zinc-500">
                {fmt(funnel.untouched_by_profiles)}{" "}
                {plural(funnel.untouched_by_profiles, "закупка", "закупки", "закупок")} с площадок
                без выбранных профилей показаны без отбора.
              </div>
            )}
          </div>
        )}

        <div className="mt-2 border-t border-white/[0.06] pt-2">
          <button
            type="button"
            onClick={act(onOpenGuide)}
            className="flex items-center gap-1 text-[11px] text-indigo-400 hover:text-indigo-300"
          >
            <CircleHelp size={12} />
            Как работает отбор?
          </button>
        </div>
      </>
    );
  }

  return (
    <div
      ref={rootRef}
      className="relative flex items-center gap-1.5"
      onMouseEnter={open}
      onMouseLeave={scheduleClose}
    >
      {counter}
      <button
        type="button"
        onClick={() => (isOpen ? setIsOpen(false) : open())}
        aria-expanded={isOpen}
        aria-label="Как отобраны закупки"
        className={`rounded-full p-0.5 transition-colors ${
          isOpen ? "text-zinc-200" : "text-zinc-600 hover:text-zinc-300"
        }`}
      >
        <Info size={14} />
      </button>
      {isLoading && <Loader2 size={12} className="animate-spin text-zinc-600" />}

      {isOpen && (
        <div
          role="dialog"
          aria-label="Как отобраны закупки"
          className="absolute left-0 top-full z-40 mt-2 w-[380px] rounded-xl border border-white/10 bg-zinc-900 p-3 text-xs shadow-[0_20px_60px_rgba(0,0,0,0.35)]"
        >
          {card}
        </div>
      )}
    </div>
  );
}
