import { useState } from "react";
import { Loader2, ThumbsDown, ThumbsUp, Undo2 } from "lucide-react";

import { api, ApiError } from "../../api/client";
import type {
  ComplianceEntry,
  ComplianceMatrix,
  ComplianceReviewReason,
  ComplianceStatusCode,
} from "../../api/types";
import { useAuth } from "../../context/useAuth";
import { complianceLabel, formatDateTime } from "../../utils/format";

/**
 * Оценка вердикта ячейки специалистом (23.10.2026, раздел 70 ARCHITECTURE.md).
 *
 * «Согласен» просто записывается. «Не согласен» требует верного вердикта, причины и пары слов
 * о том, в чём ошибка: исправление сразу меняет ячейку и процент, переживает пересчёт
 * матрицы и уходит модели прецедентом при разборе похожих требований других закупок —
 * поэтому без верного ответа и объяснения оно бесполезно.
 */

const STATUSES: ComplianceStatusCode[] = ["meets", "partial", "not_meets", "no_data"];

const REASONS: { value: ComplianceReviewReason; label: string; hint: string }[] = [
  {
    value: "fact",
    label: "Неверный факт о приборе",
    hint: "Каталог, список ПО или реестр ошибается или устарел — прецедент только для этого производителя",
  },
  {
    value: "requirement",
    label: "Неверно понято требование",
    hint: "Модель не так прочла требование — прецедент для всех производителей",
  },
  { value: "model", label: "Не та модель прибора", hint: "Сравнили не с той моделью или типом прибора" },
  { value: "other", label: "Другое", hint: "" },
];

export function ComplianceVerdictReview({
  tenderId,
  entry,
  onMatrixChange,
}: {
  tenderId: string;
  entry: ComplianceEntry;
  onMatrixChange: (matrix: ComplianceMatrix) => void;
}) {
  const { user } = useAuth();
  const [formOpen, setFormOpen] = useState(false);
  const [status, setStatus] = useState<ComplianceStatusCode | null>(null);
  const [reason, setReason] = useState<ComplianceReviewReason | null>(null);
  const [comment, setComment] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const correction = entry.reviews.find((item) => item.kind === "disagree") ?? null;
  const agreements = entry.reviews.filter((item) => item.kind === "agree");
  const myAgreement = agreements.find((item) => item.user_id === user?.id) ?? null;
  const canWithdraw = (authorId: string | null) => user?.role === "admin" || authorId === user?.id;

  const run = async (request: () => Promise<ComplianceMatrix>) => {
    setIsBusy(true);
    setError(null);
    try {
      onMatrixChange(await request());
      setFormOpen(false);
      setStatus(null);
      setReason(null);
      setComment("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить оценку");
    } finally {
      setIsBusy(false);
    }
  };

  const send = (body: Record<string, unknown>) =>
    run(() =>
      api.post<ComplianceMatrix>(`/tenders/${tenderId}/compliance/reviews`, {
        requirement_id: entry.requirement_id,
        manufacturer_id: entry.manufacturer_id,
        ...body,
      }),
    );
  const withdraw = (reviewId: string) =>
    run(() => api.delete<ComplianceMatrix>(`/tenders/${tenderId}/compliance/reviews/${reviewId}`));

  const reasonHint = REASONS.find((item) => item.value === reason)?.hint;

  return (
    <div className="mt-2 border-t border-white/[0.06] pt-2">
      {correction && (
        <div className="mb-2 rounded-md bg-indigo-500/[0.08] px-2 py-1.5 text-[11px] leading-snug text-zinc-400">
          <div className="flex items-center justify-between gap-2">
            <span>
              <span className="text-indigo-300">Исправил {correction.user_name ?? "специалист"}</span>
              <span className="text-zinc-600"> · {formatDateTime(correction.created_at)}</span>
            </span>
            {canWithdraw(correction.user_id) && (
              <button
                onClick={() => void withdraw(correction.id)}
                disabled={isBusy}
                className="flex shrink-0 items-center gap-1 text-zinc-500 hover:text-zinc-200 disabled:opacity-50"
                title="Вернуть ответ модели"
              >
                <Undo2 size={11} />
                Отозвать
              </button>
            )}
          </div>
          {correction.model_status && (
            <p className="mt-0.5 text-zinc-500">
              Модель: «{complianceLabel(correction.model_status)}»
              {correction.model_explanation ? ` — ${correction.model_explanation}` : ""}
            </p>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-1.5">
        <button
          onClick={() =>
            void (myAgreement ? withdraw(myAgreement.id) : send({ kind: "agree" }))
          }
          disabled={isBusy}
          className={`flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] transition-colors disabled:opacity-50 ${
            myAgreement
              ? "bg-emerald-500/15 text-emerald-300"
              : "text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200"
          }`}
          title={myAgreement ? "Снять согласие" : "Вердикт верный"}
        >
          <ThumbsUp size={11} />
          Согласен
        </button>
        <button
          onClick={() => setFormOpen((prev) => !prev)}
          disabled={isBusy}
          className={`flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] transition-colors disabled:opacity-50 ${
            formOpen ? "bg-red-500/15 text-red-300" : "text-zinc-500 hover:bg-white/[0.05] hover:text-zinc-200"
          }`}
          title="Исправить вердикт"
        >
          <ThumbsDown size={11} />
          Не согласен
        </button>
        {isBusy && <Loader2 size={11} className="animate-spin text-zinc-500" />}
        {agreements.length > 0 && (
          <span className="ml-auto truncate text-[11px] text-zinc-600" title="Согласились с вердиктом">
            Согласны: {agreements.map((item) => item.user_name ?? "специалист").join(", ")}
          </span>
        )}
      </div>

      {formOpen && (
        <div className="mt-2 space-y-2">
          <div>
            <div className="mb-1 text-[11px] text-zinc-500">Верный вердикт</div>
            <div className="flex flex-wrap gap-1">
              {STATUSES.filter((item) => item !== entry.status).map((item) => (
                <button
                  key={item}
                  onClick={() => setStatus(item)}
                  className={`rounded-md border px-1.5 py-0.5 text-[11px] ${
                    status === item
                      ? "border-indigo-400/50 bg-indigo-500/15 text-indigo-200"
                      : "border-white/[0.08] text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  {complianceLabel(item)}
                </button>
              ))}
            </div>
          </div>
          <div>
            <div className="mb-1 text-[11px] text-zinc-500">В чём ошибка модели</div>
            <div className="flex flex-wrap gap-1">
              {REASONS.map((item) => (
                <button
                  key={item.value}
                  onClick={() => setReason(item.value)}
                  className={`rounded-md border px-1.5 py-0.5 text-[11px] ${
                    reason === item.value
                      ? "border-indigo-400/50 bg-indigo-500/15 text-indigo-200"
                      : "border-white/[0.08] text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
            {reasonHint && <p className="mt-1 text-[11px] text-zinc-600">{reasonHint}</p>}
          </div>
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            rows={2}
            maxLength={2000}
            placeholder="Как на самом деле: «в списке Пирамиды 2.0 МИРТЕК-212 нет, только МИРТЕК-32»"
            className="w-full resize-y rounded-md border border-white/[0.08] bg-zinc-950/60 px-2 py-1.5 text-[12px] text-zinc-200 placeholder:text-zinc-600 focus:border-indigo-400/50 focus:outline-none"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={() =>
                void send({ kind: "disagree", corrected_status: status, reason, comment })
              }
              disabled={isBusy || !status || !reason || comment.trim().length < 5}
              className="rounded-md bg-indigo-500 px-2 py-1 text-[11px] font-medium text-white hover:bg-indigo-400 disabled:opacity-40"
            >
              Исправить
            </button>
            <button
              onClick={() => setFormOpen(false)}
              className="text-[11px] text-zinc-500 hover:text-zinc-200"
            >
              Отмена
            </button>
            <span className="text-[11px] text-zinc-600">Учтётся в похожих закупках</span>
          </div>
        </div>
      )}
      {error && <p className="mt-1.5 text-[11px] text-red-300">{error}</p>}
    </div>
  );
}
