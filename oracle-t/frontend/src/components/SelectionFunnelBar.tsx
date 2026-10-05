import { useState } from "react";
import { ChevronDown, CircleHelp, Loader2, X } from "lucide-react";

import type { SelectionFunnel } from "../api/types";
import { plural } from "../utils/format";

/**
 * Строка-воронка над списком тендеров (05.10.2026): «собрано → профили → модель → фильтры →
 * в списке». Заменила строку «N закупок из M собранных · по профилю»: та говорила, СКОЛЬКО
 * скрыто, но не ЧЕМ — и человек не знал, снимать ли профиль, проверку моделью или фильтр.
 *
 * Каждый слой с числом и кнопкой снять его; «Подробнее» раскрывает, сколько отобрал каждый
 * профиль и на каких площадках он действует.
 */

const fmt = (value: number) => value.toLocaleString("ru-RU");

function Layer({
  label,
  value,
  delta,
  onClear,
  clearTitle,
}: {
  label: string;
  value: number;
  delta?: number;
  onClear?: () => void;
  clearTitle?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.03] py-0.5 pl-2.5 pr-1.5">
      <span className="text-zinc-500">{label}</span>
      <span className="font-medium tabular-nums text-zinc-200">{fmt(value)}</span>
      {delta !== undefined && delta > 0 && (
        <span className="tabular-nums text-rose-300/80">−{fmt(delta)}</span>
      )}
      {onClear && (
        <button
          type="button"
          onClick={onClear}
          title={clearTitle}
          aria-label={clearTitle}
          className="rounded-full p-0.5 text-zinc-500 hover:bg-white/10 hover:text-zinc-200"
        >
          <X size={11} />
        </button>
      )}
    </span>
  );
}

const Arrow = () => <span className="text-zinc-700">→</span>;

export function SelectionFunnelBar({
  funnel,
  isLoading,
  feedLabel,
  profilesLabel,
  onClearProfiles,
  onDisableAi,
  onResetFilters,
  onOpenGuide,
  onEditProfiles,
}: {
  funnel: SelectionFunnel | null;
  isLoading: boolean;
  feedLabel: string;
  /** Подпись слоя профилей: «Профили (общие)», «Щитовые», «Без профилей». */
  profilesLabel: string;
  onClearProfiles?: () => void;
  onDisableAi?: () => void;
  onResetFilters?: () => void;
  onOpenGuide: () => void;
  onEditProfiles: () => void;
}) {
  const [isOpen, setIsOpen] = useState(false);

  if (!funnel) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-zinc-500">
        <Loader2 size={12} className="animate-spin text-zinc-600" />
        Считаем…
      </div>
    );
  }

  const profilesCut = funnel.collected - funnel.after_profiles;
  const aiCut = funnel.after_profiles - funnel.after_ai;
  const filtersCut = funnel.after_ai - funnel.shown;
  const hasProfiles = funnel.profiles.length > 0;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1.5 text-xs">
        <Layer label={`Собрано · ${feedLabel}`} value={funnel.collected} />
        <Arrow />
        <Layer
          label={profilesLabel}
          value={funnel.after_profiles}
          delta={profilesCut}
          onClear={hasProfiles ? onClearProfiles : undefined}
          clearTitle="Показать без профилей"
        />
        <Arrow />
        <Layer
          label={funnel.ai_enabled ? "Модель" : "Модель выкл."}
          value={funnel.after_ai}
          delta={aiCut}
          onClear={funnel.ai_enabled ? onDisableAi : undefined}
          clearTitle="Показать и то, что модель отклонила"
        />
        <Arrow />
        <Layer
          label="Фильтры"
          value={funnel.shown}
          delta={filtersCut}
          onClear={filtersCut > 0 ? onResetFilters : undefined}
          clearTitle="Сбросить фильтры"
        />
        <span className="ml-1 text-zinc-400">
          = <span className="font-medium tabular-nums text-zinc-100">{fmt(funnel.shown)}</span>{" "}
          {plural(funnel.shown, "закупка", "закупки", "закупок")} в списке
        </span>
        {isLoading && <Loader2 size={12} className="animate-spin text-zinc-600" />}
        <span className="ml-auto flex items-center gap-3">
          {hasProfiles && (
            <button
              type="button"
              onClick={() => setIsOpen((v) => !v)}
              aria-expanded={isOpen}
              className="flex items-center gap-1 text-zinc-500 hover:text-zinc-300"
            >
              Что отобрал каждый профиль
              <ChevronDown size={12} className={`transition-transform ${isOpen ? "rotate-180" : ""}`} />
            </button>
          )}
          <button
            type="button"
            onClick={onOpenGuide}
            className="flex items-center gap-1 text-indigo-400 hover:text-indigo-300"
          >
            <CircleHelp size={13} />
            Как это работает?
          </button>
        </span>
      </div>

      {isOpen && hasProfiles && (
        <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3 text-xs">
          <div className="mb-2 text-zinc-500">
            {funnel.profiles.length > 1
              ? `Профили на одной площадке сочетаются: ${
                  funnel.profiles_mode === "any" ? "подошёл любой" : "подошли все"
                }.`
              : "Выбран один профиль."}{" "}
            Числа — до проверки моделью и фильтров.
          </div>
          <div className="space-y-1">
            {funnel.profiles.map((item) => (
              <div key={item.profile_id} className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-zinc-200">«{item.name}»</span>
                <span className="text-zinc-500">
                  {item.is_default ? "общий" : "личный"} ·{" "}
                  {item.sources.length === 0 ? "все площадки" : item.sources.join(", ")}
                </span>
                <span className="ml-auto tabular-nums text-zinc-300">
                  {item.in_scope === 0 ? (
                    <span className="text-amber-300">не действует на этом канале</span>
                  ) : (
                    <>
                      отобрал <b className="font-medium text-zinc-100">{fmt(item.matched)}</b> из{" "}
                      {fmt(item.in_scope)}
                    </>
                  )}
                </span>
              </div>
            ))}
          </div>
          {funnel.untouched_by_profiles > 0 && (
            <div className="mt-2 text-zinc-500">
              {fmt(funnel.untouched_by_profiles)}{" "}
              {plural(funnel.untouched_by_profiles, "закупка", "закупки", "закупок")} с площадок, к
              которым не привязан ни один выбранный профиль, — показаны без отбора.
            </div>
          )}
          <button
            type="button"
            onClick={onEditProfiles}
            className="mt-2 text-indigo-400 hover:text-indigo-300"
          >
            Изменить профили…
          </button>
        </div>
      )}
    </div>
  );
}
