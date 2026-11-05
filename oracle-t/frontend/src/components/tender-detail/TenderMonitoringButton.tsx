import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Eye, EyeOff, Loader2, RefreshCw } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { TenderMonitoringState } from "../../api/types";

/**
 * «Отслеживать изменения» на странице тендера (05.11.2026, раздел 84 ARCHITECTURE.md).
 *
 * Закупка под наблюдением проверяется раз в полчаса: сроки, дата аукциона и переторжки, НМЦК,
 * обеспечение, документы. Каждое найденное изменение уходит в «Журнал событий» Bitrix24 —
 * но только если закупка уже выгружена кнопкой «Загрузить в Битрикс». Событие с ещё не
 * согласованным типом копится у нас и помечено «ждёт согласования кодов».
 */
const DELIVERY_LABELS: Record<string, string> = {
  pending: "ждёт отправки",
  held: "ждёт согласования кода",
  sent: "отправлено",
  error: "отклонено порталом",
};

export function TenderMonitoringButton({ tenderId }: { tenderId: string }) {
  const [state, setState] = useState<TenderMonitoringState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      setState(await api.get<TenderMonitoringState>(`/tenders/${tenderId}/monitoring`));
    } catch {
      setState(null);
    }
  }, [tenderId]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!state) return null;

  const run = async (action: () => Promise<TenderMonitoringState>) => {
    setBusy(true);
    setError(null);
    try {
      setState(await action());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выполнить действие");
    } finally {
      setBusy(false);
    }
  };

  const toggle = () => {
    if (state.enabled) {
      setOpen(true);
      return;
    }
    if (
      !window.confirm(
        "Поставить закупку под наблюдение? Система снимет исходные данные с площадки и ЕИС и далее будет проверять изменения раз в полчаса.",
      )
    )
      return;
    void run(() =>
      api.put<TenderMonitoringState>(`/tenders/${tenderId}/monitoring`, { enabled: true }),
    );
  };

  const fmt = (value: string | null) =>
    value ? new Date(value).toLocaleString("ru-RU") : "—";

  return (
    <>
      <button
        onClick={toggle}
        disabled={busy}
        title={
          state.enabled
            ? `Под наблюдением, проверено ${fmt(state.last_checked_at)}`
            : "Отслеживать изменения и передавать их в Bitrix24"
        }
        className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium ${
          state.enabled
            ? "bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25"
            : "bg-white/[0.06] text-zinc-300 hover:bg-white/10"
        } disabled:opacity-40`}
      >
        {busy ? <Loader2 size={13} className="animate-spin" /> : state.enabled ? <Eye size={13} /> : <EyeOff size={13} />}
        {state.enabled ? `Отслеживается · событий ${state.events.length}` : "Отслеживать изменения"}
      </button>
      {open &&
        createPortal(
          <div
            className="keynote-fade fixed inset-0 z-[100] flex items-center justify-center bg-scrim/50 p-4 backdrop-blur-sm"
            onClick={() => setOpen(false)}
          >
            <div
              role="dialog"
              aria-modal="true"
              onClick={(e) => e.stopPropagation()}
              className="max-h-[80vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-white/[0.08] bg-zinc-900 p-6 shadow-2xl"
            >
              <h3 className="text-base font-semibold text-white">Отслеживание изменений</h3>
              <p className="mt-1 text-xs text-zinc-500">
                Исходные данные сняты {fmt(state.baseline_at)}, последняя проверка{" "}
                {fmt(state.last_checked_at)} (проверок: {state.checks_count}).
                {!state.has_deal &&
                  " Закупка ещё не выгружена в Bitrix24 — события копятся и уйдут после «Загрузить в Битрикс»."}
              </p>
              {(error || state.last_error) && (
                <p className="mt-2 text-xs text-amber-300">{error ?? state.last_error}</p>
              )}
              {state.send_result?.detail && (
                <p className="mt-2 text-xs text-zinc-400">{state.send_result.detail}</p>
              )}
              <ul className="mt-4 space-y-2">
                {state.events.length === 0 && (
                  <li className="text-sm text-zinc-500">Изменений пока не найдено.</li>
                )}
                {state.events.map((event) => (
                  <li key={event.event_id} className="rounded-lg bg-white/[0.04] p-3 text-sm">
                    <div className="flex items-start justify-between gap-3">
                      <span className="font-medium text-zinc-100">{event.title}</span>
                      <span className="shrink-0 text-[11px] text-zinc-500">
                        {DELIVERY_LABELS[event.delivery] ?? event.delivery}
                      </span>
                    </div>
                    <div className="mt-1 whitespace-pre-line text-xs text-zinc-400">
                      {event.old_value ? `Было: ${event.old_value}\n` : ""}
                      {event.new_value ? `Стало: ${event.new_value}` : ""}
                    </div>
                    <div className="mt-1 text-[11px] text-zinc-600">
                      {event.event_type} · {fmt(event.detected_at)}
                      {event.error ? ` · ${event.error}` : ""}
                    </div>
                  </li>
                ))}
              </ul>
              <div className="mt-5 flex flex-wrap justify-end gap-2">
                <button
                  disabled={busy}
                  onClick={() =>
                    run(() =>
                      api.post<TenderMonitoringState>(`/tenders/${tenderId}/monitoring/check`, {}),
                    )
                  }
                  className="flex items-center gap-1.5 rounded-lg bg-white/[0.08] px-3 py-1.5 text-xs text-zinc-200 hover:bg-white/[0.12] disabled:opacity-40"
                >
                  <RefreshCw size={13} className={busy ? "animate-spin" : ""} />
                  Проверить сейчас
                </button>
                <button
                  disabled={busy}
                  onClick={() => {
                    if (!window.confirm("Снять закупку с наблюдения?")) return;
                    void run(() =>
                      api.put<TenderMonitoringState>(`/tenders/${tenderId}/monitoring`, {
                        enabled: false,
                      }),
                    ).then(() => setOpen(false));
                  }}
                  className="rounded-lg bg-white/[0.08] px-3 py-1.5 text-xs text-zinc-200 hover:bg-white/[0.12] disabled:opacity-40"
                >
                  Снять с наблюдения
                </button>
                <button
                  onClick={() => setOpen(false)}
                  className="rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-snow hover:bg-indigo-400"
                >
                  Закрыть
                </button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
