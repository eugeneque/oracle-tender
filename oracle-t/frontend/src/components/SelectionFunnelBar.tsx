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

/** Шаг отбора (редизайн 23.10.2026): номер на линии-«нитке», число справа и полоса доли
 *  от собранного — чтобы глазами было видно, какой слой режет больше всего. */
function Step({
  index,
  label,
  hint,
  value,
  total,
  delta,
  action,
  last = false,
}: {
  index: number;
  label: string;
  hint?: string;
  value: number;
  total: number;
  delta?: number;
  action?: ReactNode;
  last?: boolean;
}) {
  const share = total > 0 ? value / total : 0;
  return (
    <div className="relative flex gap-3">
      {!last && <span className="absolute bottom-0 left-[11px] top-7 w-px bg-white/[0.08]" />}
      <span
        className={`relative mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold tabular-nums ${
          last ? "bg-indigo-500/20 text-indigo-200 ring-1 ring-indigo-400/40" : "nav-well text-zinc-400"
        }`}
      >
        {index}
      </span>
      <div className={`min-w-0 flex-1 ${last ? "" : "pb-3.5"}`}>
        <div className="flex items-baseline gap-2">
          <span className="min-w-0 flex-1 truncate font-medium text-zinc-100">{label}</span>
          {delta !== undefined && delta > 0 && (
            <span className="shrink-0 rounded-full bg-rose-500/10 px-1.5 py-px text-[10px] tabular-nums text-rose-300">
              −{fmt(delta)}
            </span>
          )}
          <span className="shrink-0 text-[13px] font-semibold tabular-nums text-white">{fmt(value)}</span>
        </div>
        {hint && <div className="mt-0.5 text-[11px] leading-snug text-zinc-500">{hint}</div>}
        <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className="step-fill h-full rounded-full"
            style={{ width: `${Math.max(1.5, Math.round(share * 1000) / 10)}%` }}
          />
        </div>
        {action && <div className="mt-1.5">{action}</div>}
      </div>
    </div>
  );
}

function LinkButton({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full bg-white/[0.05] px-2 py-0.5 text-[11px] text-zinc-300 transition-colors hover:bg-indigo-500/15 hover:text-indigo-200"
    >
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
        <div className="mb-3">
          <div className="text-[15px] font-semibold text-white">Как отобраны закупки</div>
          <div className="text-xs text-zinc-500">Каждый слой сужает список — снимите лишний</div>
        </div>
        <div>
          <Step index={1} label="Собрано" hint={feedLabel} value={funnel.collected} total={funnel.collected} />
          <Step
            index={2}
            total={funnel.collected}
            label={profilesLabel}
            hint="Ключевые слова и коды ОКПД2 профилей отбора"
            value={funnel.after_profiles}
            delta={profilesCut}
            action={
              <span className="flex flex-wrap gap-1.5">
                {hasProfiles && onClearProfiles && (
                  <LinkButton onClick={act(onClearProfiles)}>Показать без профилей</LinkButton>
                )}
                <LinkButton onClick={act(onEditProfiles)}>Изменить профили…</LinkButton>
              </span>
            }
          />
          <Step
            index={3}
            total={funnel.collected}
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
            index={4}
            total={funnel.collected}
            last
            label="Фильтры — в списке"
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
          <div className="mt-3 border-t border-white/[0.06] pt-3">
            <div className="mb-2 text-[11px] text-zinc-500">
              {funnel.profiles.length > 1
                ? `Что отобрал каждый профиль (на одной площадке — ${
                    funnel.profiles_mode === "any" ? "подошёл любой" : "подошли все"
                  }):`
                : "Что отобрал профиль:"}
            </div>
            <div className="max-h-44 space-y-2 overflow-y-auto pr-1">
              {funnel.profiles.map((item) => (
                <div key={item.profile_id}>
                  <div className="flex items-baseline gap-2">
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
                  {item.in_scope > 0 && (
                    <div className="mt-1 h-0.5 overflow-hidden rounded-full bg-white/[0.05]">
                      <div
                        className="h-full rounded-full bg-zinc-400/60"
                        style={{ width: `${Math.max(1, (item.matched / item.in_scope) * 100)}%` }}
                      />
                    </div>
                  )}
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

        <div className="mt-3 border-t border-white/[0.06] pt-3">
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
          className="nav-popover absolute left-0 top-full z-40 mt-2 w-[400px] rounded-[22px] p-4 text-xs"
        >
          {card}
        </div>
      )}
    </div>
  );
}
