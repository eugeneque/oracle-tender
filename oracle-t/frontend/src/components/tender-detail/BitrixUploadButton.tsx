import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  ExternalLink,
  Loader2,
  Lock,
  Upload,
} from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { BitrixTenderState, TenderStage } from "../../api/types";

/**
 * «Загрузить в Битрикс» на странице тендера (20.10.2026): закупка с анализом уходит в сделку
 * тестовой воронки портала — короткие поля и строки вкладки «ИИ анализ и решения».
 *
 * Доступна для проверенной закупки (этап «На проверке» и дальше). Первое нажатие создаёт
 * сделку, следующие обновляют её же. Если портал отказал — текст отказа рядом с кнопкой:
 * его передают администратору Bitrix24. `stage` нужен, чтобы кнопка ожила сразу после
 * смены этапа в карточке, без перезагрузки страницы.
 *
 * `compact` — та же кнопка в правой панели списка (20.10.2026): «Отправить в Битрикс» в
 * ряду с «Комментариями» и «Избранным». С подписью и мягкой заливкой без рамки: иконкой без
 * подписи (редизайн шапки 20.10.2026) кнопку было не найти. Что уже
 * выгружено или чем отказал портал — в подсказке и точкой на значке.
 *
 * Недоступная выгрузка (20.10.2026): кнопка выглядит заблокированной, но нажимается — и
 * объясняет в попапе, почему закупку нельзя отправить (этап, Битрикс не подключён, выгрузка
 * выключена). Раньше причина пряталась в подсказке у неактивной кнопки, и её не находили.
 */
export function BitrixUploadButton({
  tenderId,
  stage,
  compact = false,
}: {
  tenderId: string;
  stage: TenderStage;
  compact?: boolean;
}) {
  const [state, setState] = useState<BitrixTenderState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  // Какой попап открыт: «нельзя отправить» (кнопка заблокирована) или «портал отказал».
  const [popup, setPopup] = useState<"blocked" | "failed" | null>(null);

  const load = useCallback(async () => {
    try {
      setState(
        await api.get<BitrixTenderState>(
          `/integrations/bitrix24/tenders/${tenderId}`,
        ),
      );
    } catch {
      // Кнопка — дополнение к странице: если состояние не загрузилось, её просто нет.
      setState(null);
    }
  }, [tenderId]);

  useEffect(() => {
    void load();
  }, [load, stage]);

  if (!state) return null;

  const upload = async () => {
    if (!state.can_upload) {
      setPopup("blocked");
      return;
    }
    const question = state.deal_id
      ? `Обновить сделку ${state.deal_id} в Bitrix24 данными и анализом этой закупки?`
      : "Создать в Bitrix24 сделку по этой закупке и выгрузить в неё анализ?";
    if (!window.confirm(question)) return;
    setIsBusy(true);
    setError(null);
    try {
      const next = await api.post<BitrixTenderState>(
        `/integrations/bitrix24/tenders/${tenderId}`,
        {},
      );
      setState(next);
      if (next.last_request && !next.last_request.accepted)
        setError(next.last_request.error);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Не удалось выгрузить в Bitrix24",
      );
    } finally {
      setIsBusy(false);
    }
  };

  const last = state.last_request;
  const lastFailed = last && !last.accepted;
  const sentAt =
    last?.accepted && last.sent_at
      ? new Date(last.sent_at).toLocaleString("ru-RU")
      : null;

  const hint =
    state.reason ??
    (error || lastFailed
      ? `Bitrix24 отказал: ${error ?? last?.error}. Нажмите, чтобы попробовать снова`
      : state.deal_id
        ? `Сделка ${state.deal_id} в Bitrix24${sentAt ? `, выгружено ${sentAt}` : ""} — нажмите, чтобы обновить`
        : "Создать в Bitrix24 сделку с анализом закупки");

  const blocked = !state.can_upload;
  const failText = error ?? last?.error ?? null;
  const failed = Boolean(error || lastFailed);
  const reasonPopup =
    popup &&
    createPortal(
      popup === "blocked" ? (
        <BlockedReasonPopup
          title="Нельзя отправить в Битрикс"
          reason={state.reason ?? "Отправка в Bitrix24 сейчас недоступна."}
          advice={blockedAdvice(state.reason ?? "")}
          dealId={state.deal_id}
          dealUrl={state.deal_url}
          onClose={() => setPopup(null)}
        />
      ) : (
        <BlockedReasonPopup
          title={
            state.deal_id
              ? "Битрикс не обновил сделку"
              : "Битрикс не создал сделку"
          }
          reason={failText ?? "Портал отклонил выгрузку."}
          advice={failedAdvice(failText ?? "")}
          dealId={state.deal_id}
          dealUrl={state.deal_url}
          onClose={() => setPopup(null)}
        />
      ),
      document.body,
    );

  if (compact) {
    return (
      <>
        {reasonPopup}
        {/* Ссылка на сделку и в компактной шапке (20.10.2026): раньше номер сделки был
            только в подсказке, а открыть её в Битриксе можно было лишь с отдельной страницы. */}
        {state.deal_url && (
          <a
            href={state.deal_url}
            target="_blank"
            rel="noreferrer"
            title={
              sentAt
                ? `Открыть сделку в Bitrix24 · выгружено ${sentAt}`
                : "Открыть сделку в Bitrix24"
            }
            className="flex h-8 items-center gap-1 whitespace-nowrap rounded-lg px-2 text-xs text-indigo-300 transition-colors hover:bg-white/5 hover:text-indigo-200"
          >
            Сделка {state.deal_id}
            <ExternalLink size={12} />
          </a>
        )}
        {/* Отказ портала словами (20.10.2026): ссылки на сделку нет, потому что сделка не
            создалась, — а жёлтую точку на кнопке никто не связывал с отказом. */}
        {!state.deal_url && failed && (
          <button
            onClick={() => setPopup("failed")}
            title={failText ?? undefined}
            className="flex h-8 items-center gap-1 whitespace-nowrap rounded-lg px-2 text-xs text-amber-300 transition-colors hover:bg-amber-500/10"
          >
            <AlertTriangle size={13} />
            Сделка не создана
          </button>
        )}
        <button
          onClick={() => void upload()}
          disabled={isBusy}
          aria-disabled={blocked}
          title={
            blocked
              ? "Отправка недоступна — нажмите, чтобы узнать почему"
              : hint
          }
          className={`relative mx-1 flex h-8 items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 text-xs font-medium transition-colors ${
            blocked
              ? "cursor-help bg-white/[0.03] text-zinc-600 hover:text-zinc-400"
              : "bg-white/[0.06] text-zinc-300 hover:bg-white/10 hover:text-white disabled:opacity-40"
          }`}
        >
          {isBusy ? (
            <Loader2 size={14} className="animate-spin" />
          ) : blocked ? (
            <Lock size={13} />
          ) : (
            <Upload size={14} />
          )}
          Отправить в Битрикс
          {state.deal_id && failed && (
            <span
              className={`absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full ${
                error || lastFailed ? "bg-amber-400" : "bg-indigo-400"
              }`}
            />
          )}
        </button>
      </>
    );
  }

  return (
    <div className="flex items-center gap-2">
      {(error || lastFailed) && (
        <span
          className="flex max-w-[360px] items-center gap-1 truncate text-xs text-amber-300"
          title={error ?? last?.error ?? undefined}
        >
          <AlertTriangle size={13} className="shrink-0" />
          <span className="truncate">{error ?? last?.error}</span>
        </span>
      )}
      {state.deal_url && (
        <a
          href={state.deal_url}
          target="_blank"
          rel="noreferrer"
          title={sentAt ? `Выгружено ${sentAt}` : "Сделка в Bitrix24"}
          className="flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-200"
        >
          Сделка {state.deal_id}
          <ExternalLink size={12} />
        </a>
      )}
      {reasonPopup}
      <button
        onClick={() => void upload()}
        disabled={isBusy}
        aria-disabled={blocked}
        title={
          blocked
            ? "Отправка недоступна — нажмите, чтобы узнать почему"
            : state.deal_id
              ? "Обновить сделку в Bitrix24"
              : "Создать сделку в Bitrix24"
        }
        className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium ${
          blocked
            ? "cursor-help bg-indigo-500/30 text-snow/60 hover:bg-indigo-500/40"
            : "bg-indigo-500 text-snow hover:bg-indigo-400 disabled:opacity-40"
        }`}
      >
        {isBusy ? (
          <Loader2 size={13} className="animate-spin" />
        ) : blocked ? (
          <Lock size={13} />
        ) : (
          <Upload size={13} />
        )}
        Загрузить в Битрикс
      </button>
    </div>
  );
}

/** Попап с причиной, почему выгрузка в Битрикс недоступна или почему портал её отклонил. Закрывается кнопкой, кликом по
 * затемнению и Esc. Совет под причиной — что сделать, чтобы кнопка ожила. */
function blockedAdvice(reason: string): string {
  if (reason.includes("отклонена"))
    return "Если закупку отклонили по ошибке, верните её на этап «На проверке» — кнопка станет доступна.";
  if (reason.includes("«На проверке»"))
    return "Этап меняется в строке под названием закупки.";
  return "Настройки интеграции — в разделе «Настройки → Bitrix24».";
}

function failedAdvice(error: string): string {
  if (error.includes("ИНН"))
    return "В карточке закупки не нашлось ИНН заказчика, а без него портал не находит компанию. Обычно ИНН приходит с площадки в «Сведениях о заказчике»; после обновления карточки отправьте ещё раз.";
  return "Передайте этот текст администратору Bitrix24 и попробуйте отправить ещё раз.";
}

function BlockedReasonPopup({
  title,
  reason,
  advice,
  dealId,
  dealUrl,
  onClose,
}: {
  title: string;
  reason: string;
  advice: string;
  dealId: number | null;
  dealUrl: string | null;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="keynote-fade fixed inset-0 z-[100] flex items-center justify-center bg-scrim/50 p-4 backdrop-blur-sm"
      onClick={(e) => {
        e.stopPropagation();
        onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="bitrix-blocked-title"
        onClick={(e) => e.stopPropagation()}
        className="bitrix-pop w-full max-w-sm cursor-default rounded-2xl border border-white/[0.08] bg-zinc-900 p-6 text-center shadow-2xl"
      >
        <div className="relative mx-auto mb-4 flex h-14 w-14 items-center justify-center">
          <span className="bitrix-ring absolute inset-0 rounded-full bg-amber-400/30" />
          <span className="bitrix-ring-late absolute inset-0 rounded-full bg-amber-400/20" />
          <span className="bitrix-wobble relative flex h-14 w-14 items-center justify-center rounded-full bg-amber-500/15 text-amber-300 ring-1 ring-amber-400/40">
            {title.startsWith("Нельзя") ? <Lock size={24} /> : <AlertTriangle size={24} />}
          </span>
        </div>
        <h3
          id="bitrix-blocked-title"
          className="text-base font-semibold text-white"
        >
          {title}
        </h3>
        <p className="mt-2 text-sm leading-relaxed text-zinc-300">{reason}</p>
        <p className="mt-2 text-xs leading-relaxed text-zinc-500">{advice}</p>
        {/* Закупку могли выгрузить раньше, до отклонения: сделка в Битриксе при этом есть. */}
        {dealUrl && (
          <a
            href={dealUrl}
            target="_blank"
            rel="noreferrer"
            className="mt-4 inline-flex items-center gap-1 text-sm text-indigo-300 hover:text-indigo-200"
          >
            Ранее выгружено: сделка {dealId}
            <ExternalLink size={13} />
          </a>
        )}
        <button
          autoFocus
          onClick={onClose}
          className="mt-5 w-full rounded-lg bg-white/[0.08] py-2 text-sm font-medium text-zinc-200 transition-colors hover:bg-white/[0.12]"
        >
          Понятно
        </button>
      </div>
    </div>
  );
}
