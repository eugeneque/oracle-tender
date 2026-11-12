import type { TenderMonitoringState } from "../../api/types";
import { formatDateTime } from "../../utils/format";

/**
 * Изменения закупки, найденные наблюдением (12.11.2026, раздел 100 ARCHITECTURE.md), — во
 * вкладке «Журнал событий» над таблицей событий из карточки ЕИС.
 *
 * Те же события уходят в «Журнал событий» Bitrix24. Новые для пользователя (появились после
 * его прошлого захода в журнал) отмечены зелёной точкой; изменения, которые могут повлиять
 * на разбор ИИ, подписаны отдельно — по ним разбор помечается «Информация неактуальная».
 */
const DELIVERY_LABELS: Record<string, string> = {
  pending: "ждёт отправки в Bitrix24",
  held: "ждёт согласования кода",
  sent: "в Bitrix24",
  error: "Bitrix24 не принял",
};

export const newEventsDot = (
  <span
    aria-label="Новое"
    className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]"
  />
);

export function TenderEventsJournal({ state }: { state: TenderMonitoringState | null }) {
  if (!state || (!state.enabled && state.events.length === 0)) return null;
  return (
    <section className="mb-6">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold text-white">Изменения по закупке</h4>
        <span className="text-[11px] text-zinc-500">
          {state.enabled
            ? `${state.auto ? "Отслеживается с выгрузки в Bitrix24" : "Отслеживается"} · проверено ${
                state.last_checked_at ? formatDateTime(state.last_checked_at) : "—"
              }`
            : "Наблюдение снято"}
        </span>
      </div>
      {state.events.length === 0 ? (
        <p className="text-sm text-zinc-500">Изменений пока не найдено.</p>
      ) : (
        <ul className="divide-y divide-white/[0.06] rounded-xl border border-white/[0.08]">
          {state.events.map((event) => (
            <li key={event.event_id} className="flex gap-3 px-3.5 py-2.5">
              <span className="mt-[7px] w-1.5 shrink-0">{event.is_new && newEventsDot}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                  <span className={`text-sm ${event.is_new ? "font-medium text-white" : "text-zinc-200"}`}>
                    {event.title}
                  </span>
                  <span className="text-[11px] text-zinc-500">{formatDateTime(event.detected_at)}</span>
                </div>
                {(event.old_value || event.new_value) && (
                  <div className="mt-0.5 whitespace-pre-line break-words text-xs text-zinc-400">
                    {event.old_value && <span className="text-zinc-500 line-through">{event.old_value}</span>}
                    {event.old_value && event.new_value && " → "}
                    {event.new_value}
                  </div>
                )}
                <div className="mt-0.5 flex flex-wrap gap-x-2 text-[11px] text-zinc-600">
                  <span>{DELIVERY_LABELS[event.delivery] ?? event.delivery}</span>
                  {event.affects_analysis && <span className="text-amber-300/80">влияет на разбор ИИ</span>}
                  {event.error && <span className="text-red-300/80">{event.error}</span>}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
