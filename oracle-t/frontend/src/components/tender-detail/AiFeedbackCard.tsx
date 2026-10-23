import { useState } from "react";
import { ArrowRight, Loader2, Repeat2, ThumbsDown, ThumbsUp } from "lucide-react";
import { Link } from "react-router-dom";

import { api, ApiError } from "../../api/client";
import type { AiFeedback, ConclusionSnapshot } from "../../api/types";
import { useAuth } from "../../context/useAuth";
import { formatDateTime, verdictLabel } from "../../utils/format";

/**
 * Ответ специалиста на заключение ИИ (28.09.2026): кто, когда, что написал, каким было
 * заключение до и каким стало после. Одна карточка на двоих — «Комментарии» в закупке и
 * «Ответы специалистов» в настройках: пара «до / после» должна читаться одинаково.
 */

const STATUS_TEXT: Record<AiFeedback["status"], string> = {
  recorded: "",
  pending: "ждёт пересмотра",
  processing: "ИИ пересматривает",
  applied: "учтено",
  error: "пересмотр не удался",
};

function Snapshot({ title, value }: { title: string; value: ConclusionSnapshot | null }) {
  if (!value) {
    return (
      <div className="min-w-0 flex-1 rounded-lg bg-white/[0.03] p-2.5">
        <div className="text-[11px] text-zinc-500">{title}</div>
        <div className="text-xs text-zinc-600">—</div>
      </div>
    );
  }
  const products = value.our_products.map((item) => item.model).join(", ");
  return (
    <div className="min-w-0 flex-1 rounded-lg bg-white/[0.03] p-2.5">
      <div className="flex flex-wrap items-baseline gap-x-2 text-[11px] text-zinc-500">
        {title}
        {value.fit_label && <span className="text-zinc-300">{value.fit_label}</span>}
        {value.verdict && <span>· {verdictLabel(value.verdict)}</span>}
      </div>
      {value.headline && <p className="mt-1 text-xs leading-relaxed text-zinc-300">{value.headline}</p>}
      {products && <p className="mt-1 text-[11px] text-zinc-500">Приборы: {products}</p>}
    </div>
  );
}

/** Переносить ли несогласие в похожие закупки (23.10.2026, раздел 71 ARCHITECTURE.md). Менять
 * может автор или администратор: замечание о частностях одной закупки выключают. */
function PrecedentToggle({ item }: { item: AiFeedback }) {
  const { user } = useAuth();
  const [shared, setShared] = useState(item.share_as_precedent);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canEdit = user?.role === "admin" || (item.user_id !== null && item.user_id === user?.id);

  const toggle = async () => {
    setIsBusy(true);
    setError(null);
    try {
      const next = await api.patch<AiFeedback>(`/tenders/${item.tender_id}/ai-score/feedback/${item.id}`, {
        share_as_precedent: !shared,
      });
      setShared(next.share_as_precedent);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить");
    } finally {
      setIsBusy(false);
    }
  };

  const label = shared ? "Учитывается в похожих закупках" : "Только для этой закупки";
  return (
    <div className="mt-2 text-[11px]">
      {canEdit ? (
        <button
          onClick={() => void toggle()}
          disabled={isBusy}
          className={`flex items-center gap-1 rounded-md px-1.5 py-0.5 transition-colors disabled:opacity-50 ${
            shared ? "bg-indigo-500/10 text-indigo-300" : "text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200"
          }`}
          title={
            shared
              ? "ИИ получит это возражение при разборе похожих закупок. Нажмите, чтобы оставить только для этой"
              : "Нажмите, чтобы ИИ учитывал возражение и в похожих закупках"
          }
        >
          {isBusy ? <Loader2 size={11} className="animate-spin" /> : <Repeat2 size={11} />}
          {label}
        </button>
      ) : (
        <span className={`flex items-center gap-1 ${shared ? "text-indigo-300/80" : "text-zinc-600"}`}>
          <Repeat2 size={11} />
          {label}
        </span>
      )}
      {error && <p className="mt-1 text-red-300">{error}</p>}
    </div>
  );
}

export function AiFeedbackCard({ item, showTender = false }: { item: AiFeedback; showTender?: boolean }) {
  const disagree = item.kind === "disagree";
  const status = STATUS_TEXT[item.status];
  return (
    <article className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
      {showTender && (
        <Link
          to={`/tenders/${item.tender_id}`}
          className="mb-1 block truncate text-sm font-medium text-zinc-100 hover:text-indigo-300"
          title="Открыть закупку"
        >
          {item.tender_title ?? "Закупка"}
        </Link>
      )}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
        <span
          className={`flex items-center gap-1 rounded-md px-1.5 py-0.5 ${
            disagree ? "bg-red-500/10 text-red-300" : "bg-emerald-500/10 text-emerald-300"
          }`}
        >
          {disagree ? <ThumbsDown size={11} /> : <ThumbsUp size={11} />}
          {disagree ? "Не согласен" : "Согласен"}
        </span>
        <span className="text-zinc-300">{item.user_name ?? "Специалист"}</span>
        <span className="text-zinc-600">{formatDateTime(item.created_at)}</span>
        {showTender && item.source_name && (
          <span className="text-zinc-600">
            · {item.source_name}
            {item.tender_external_id ? ` № ${item.tender_external_id}` : ""}
          </span>
        )}
        {status && (
          <span
            className={`ml-auto flex items-center gap-1 ${
              item.status === "error" ? "text-red-300" : item.status === "applied" ? "text-zinc-500" : "text-indigo-300"
            }`}
          >
            {(item.status === "pending" || item.status === "processing") && (
              <Loader2 size={11} className="animate-spin" />
            )}
            {status}
          </span>
        )}
      </div>

      {item.text && <p className="mt-2 whitespace-pre-line text-sm leading-relaxed text-zinc-200">{item.text}</p>}

      {disagree ? (
        <>
          <div className="mt-2.5 flex flex-col gap-2 sm:flex-row sm:items-stretch">
            <Snapshot title="Было" value={item.before} />
            <ArrowRight size={14} className="hidden shrink-0 self-center text-zinc-600 sm:block" />
            <Snapshot title="Стало" value={item.after} />
          </div>
          {item.ai_response && (
            <p className="mt-2 border-l-2 border-indigo-400/50 pl-2.5 text-xs leading-relaxed text-zinc-400">
              <span className="text-indigo-300">Ответ ИИ: </span>
              {item.ai_response}
            </p>
          )}
          {item.status === "error" && item.error && (
            <p className="mt-2 text-xs text-red-300">{item.error}</p>
          )}
          <PrecedentToggle item={item} />
        </>
      ) : (
        item.before?.headline && (
          <p className="mt-1.5 text-xs text-zinc-500">На заключение: «{item.before.headline}»</p>
        )
      )}
    </article>
  );
}
