import { useEffect, useState } from "react";
import { AlertTriangle, Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type {
  CompanyParticipation,
  ParticipationList,
  ParticipationOutcome,
  ParticipationSummary,
  ParticipationSyncResult,
  ParticipationWrite,
} from "../../api/types";
import { formatDate, formatDateTime, formatPrice, plural } from "../../utils/format";

/**
 * История участий МИРТЕК (раздел 5.6 «Моя компания», раздел 7 ТЗ).
 *
 * Основной источник измерения «История» AI-оценки: пока здесь пусто, History по каждому
 * тендеру честно показывает «недостаточно данных» и исключается из итога, а не считается
 * нулём.
 *
 * Сводка намеренно разносит «проигрыш» и «дисквалификацию». Это разные диагнозы: проиграли
 * по цене — работать с ценой, сняли с торгов по формальной причине — работать с оформлением
 * заявки. Слитый счётчик подсказал бы неверное действие. По той же причине win-rate считается
 * от участий с ИЗВЕСТНЫМ исходом: неразобранные записи не должны занижать его молча.
 *
 * Наполняется выгрузкой из реестра контрактов ЕИС по ИНН компании. У источника есть
 * особенность, о которой интерфейс обязан говорить вслух: в реестре лежат только заключённые
 * контракты, то есть одни победы. Пока в истории нет ничего, кроме них, win-rate равен 100%
 * по устройству выборки, а не по заслугам — отсюда предупреждение под сводкой и отказ
 * измерения «История» считать долю побед по таким данным.
 */

const inputClass =
  "mt-1 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";

const OUTCOME_OPTIONS: Array<{ value: ParticipationOutcome; label: string }> = [
  { value: "won", label: "Победа" },
  { value: "lost", label: "Проигрыш" },
  { value: "disqualified", label: "Дисквалификация" },
  { value: "unknown", label: "Исход неизвестен" },
];

const OUTCOME_CLASS: Record<ParticipationOutcome, string> = {
  won: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  lost: "border-red-500/30 bg-red-500/10 text-red-300",
  disqualified: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  unknown: "border-white/10 bg-white/5 text-zinc-500",
};

const EMPTY_FORM: ParticipationWrite = {
  external_tender_id: "",
  tender_title: "",
  customer_name: "",
  our_bid: "",
  final_contract_value: "",
  executed_at: "",
  outcome: "unknown",
  lessons_learned_md: "",
};

function SummaryTile({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: string;
  hint?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] px-3 py-2.5">
      <div className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`mt-0.5 text-lg font-semibold ${accent ?? "text-zinc-100"}`}>{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-zinc-600">{hint}</div>}
    </div>
  );
}

function Summary({ summary }: { summary: ParticipationSummary }) {
  const decided = summary.won + summary.lost + summary.disqualified;
  const winRate = summary.win_rate === null ? null : Math.round(Number(summary.win_rate));
  return (
    <>
    <div className="mb-4 grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
      <SummaryTile label="Участий" value={String(summary.total)} />
      <SummaryTile label="Побед" value={String(summary.won)} accent="text-emerald-300" />
      <SummaryTile label="Проигрышей" value={String(summary.lost)} accent="text-red-300" />
      <SummaryTile
        label="Дисквалификаций"
        value={String(summary.disqualified)}
        accent="text-amber-300"
        hint="не допустили к торгам"
      />
      <SummaryTile
        label="Win-rate"
        value={winRate === null ? "нет данных" : `${winRate}%`}
        hint={
          winRate === null
            ? "нет участий с известным исходом"
            : summary.wins_only_data
              ? "по выборке без проигрышей — см. ниже"
              : `от ${decided} ${plural(decided, "участия", "участий", "участий")} с известным исходом`
        }
      />
    </div>
    {summary.wins_only_data && (
      <div className="mb-4 flex gap-2 rounded-lg border border-amber-500/25 bg-amber-500/[0.07] px-3 py-2.5">
        <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-400" />
        <p className="text-[11px] leading-relaxed text-amber-200/90">
          Вся история выгружена из реестра контрактов ЕИС, где есть только заключённые
          контракты — проигрышей там нет по устройству. Поэтому win-rate здесь равен 100% как
          свойство выборки, а не как факт о компании, и измерение «История» по таким данным
          долю побед не считает. Проигрыши подтянутся сами по закупкам, отмеченным как
          «заявка подана»; остальные можно добавить вручную.
        </p>
      </div>
    )}
    </>
  );
}

function ManualForm({ onSaved }: { onSaved: () => void }) {
  const [form, setForm] = useState<ParticipationWrite>(EMPTY_FORM);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const patch = (changes: Partial<ParticipationWrite>) =>
    setForm((prev) => ({ ...prev, ...changes }));

  const save = async () => {
    setIsSaving(true);
    setError(null);
    try {
      // Пустые строки в необязательных полях отправлять нельзя: они попадут в базу как
      // «значение есть, оно пустое» и потом будут неотличимы от заполненных.
      const payload: Record<string, unknown> = { outcome: form.outcome };
      for (const [key, value] of Object.entries(form)) {
        if (key === "outcome") continue;
        if (value !== "" && value !== null && value !== undefined) payload[key] = value;
      }
      await api.post<CompanyParticipation>("/company-participations", payload);
      setForm(EMPTY_FORM);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сохранить запись");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="mb-4 rounded-xl border border-white/[0.08] bg-black/20 p-4">
      <h4 className="mb-1 text-xs font-semibold text-zinc-200">Добавить участие вручную</h4>
      <p className="mb-3 text-[11px] text-zinc-600">
        Для закупок, которые не подхватились автоматически: снятия с торгов, участие в
        закупках вне ЕИС и всё, что было до появления системы. Повторная выгрузка такие
        записи не трогает.
      </p>
      {error && (
        <div className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
          {error}
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label className="block text-xs text-zinc-400 lg:col-span-2">
          Закупка
          <input
            value={form.tender_title ?? ""}
            onChange={(e) => patch({ tender_title: e.target.value })}
            placeholder="Поставка приборов учёта электроэнергии"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Номер закупки
          <input
            value={form.external_tender_id ?? ""}
            onChange={(e) => patch({ external_tender_id: e.target.value })}
            placeholder="0158300012325000123"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Заказчик (юрлицо)
          <input
            value={form.customer_name ?? ""}
            onChange={(e) => patch({ customer_name: e.target.value })}
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Итог
          <select
            value={form.outcome}
            onChange={(e) => patch({ outcome: e.target.value as ParticipationOutcome })}
            className={inputClass}
          >
            {OUTCOME_OPTIONS.map((option) => (
              <option key={option.value} value={option.value} className="bg-zinc-900">
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block text-xs text-zinc-400">
          Дата
          <input
            type="date"
            value={form.executed_at ?? ""}
            onChange={(e) => patch({ executed_at: e.target.value })}
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Наша ставка, ₽
          <input
            value={form.our_bid ?? ""}
            onChange={(e) => patch({ our_bid: e.target.value })}
            placeholder="1250000"
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400">
          Цена контракта, ₽
          <input
            value={form.final_contract_value ?? ""}
            onChange={(e) => patch({ final_contract_value: e.target.value })}
            className={inputClass}
          />
        </label>
        <label className="block text-xs text-zinc-400 lg:col-span-3">
          Выводы и заметки
          <textarea
            value={form.lessons_learned_md ?? ""}
            onChange={(e) => patch({ lessons_learned_md: e.target.value })}
            rows={2}
            placeholder="Что стоит учесть в следующий раз"
            className={inputClass}
          />
        </label>
      </div>
      <button
        onClick={() => void save()}
        disabled={isSaving}
        className="mt-3 flex items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-4 py-2 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
      >
        {isSaving && <Loader2 size={12} className="animate-spin" />}
        Добавить запись
      </button>
    </div>
  );
}

export function CompanyParticipationsPanel({
  isAdmin,
  hasInn,
}: {
  isAdmin: boolean;
  /** Синхронизация идёт по ИНН из профиля: без него кнопка бессмысленна. */
  hasInn: boolean;
}) {
  const [data, setData] = useState<ParticipationList | null>(null);
  const [isSyncing, setIsSyncing] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = async () => {
    try {
      setData(await api.get<ParticipationList>("/company-participations"));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить историю участий");
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const sync = async () => {
    setIsSyncing(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.post<ParticipationSyncResult>(
        "/company-participations/sync",
        {},
      );
      setNotice(
        `Победы из реестра контрактов: получено ${result.fetched}, добавлено ` +
          `${result.created}, обновлено ${result.updated}, сопоставлено с тендерами ` +
          `${result.matched_tenders}. Проигрыши: проверено закупок с поданной заявкой ` +
          `${result.checked_submitted}, записано ${result.losses_found}.`,
      );
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Выгрузка из реестра ЕИС не удалась");
    } finally {
      setIsSyncing(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await api.delete(`/company-participations/${id}`);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось удалить запись");
    }
  };

  return (
    <div>
      <p className="mb-4 text-xs leading-relaxed text-zinc-500">
        Реальная история участия компании в закупках — основной источник измерения «История»
        AI-оценки. Победы выгружаются из реестра контрактов ЕИС по ИНН. Проигрыши в открытых
        реестрах не публикуются вовсе — участники электронных процедур обезличены, раскрывают
        только победителя, — поэтому они выводятся из пайплайна: закупка с отметкой «заявка
        подана» завершилась, контракт достался не нам. Что не покрылось и этим, вносится
        вручную.{" "}
        {data && data.summary.total === 0
          ? "Пока записей нет, «История» по тендерам показывает «недостаточно данных» и исключается из итоговой оценки, а не считается нулём."
          : "По каждому тендеру в расчёт идут участия в похожих закупках и у того же заказчика."}
      </p>

      {error && (
        <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}
      {notice && (
        <div className="mb-4 rounded-lg border border-indigo-500/20 bg-indigo-500/[0.06] px-4 py-2.5 text-sm text-indigo-300">
          {notice}
        </div>
      )}

      {isAdmin && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <button
            onClick={() => void sync()}
            disabled={isSyncing || !hasInn}
            title={hasInn ? "Победы — из реестра контрактов по ИНН, проигрыши — по закупкам с поданной заявкой" : "Сначала укажите ИНН на вкладке «Профиль компании»"}
            className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-indigo-500 to-violet-600 px-4 py-2 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            {isSyncing ? (
              <Loader2 size={13} className="animate-spin" />
            ) : (
              <RefreshCw size={13} />
            )}
            Обновить историю из ЕИС
          </button>
          <button
            onClick={() => setShowForm((v) => !v)}
            className="flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-2 text-xs text-zinc-300 hover:bg-white/5"
          >
            <Plus size={13} />
            Добавить вручную
          </button>
          {!hasInn && (
            <span className="text-[11px] text-amber-400">
              ИНН не заполнен — реестр контрактов ищется именно по нему.
            </span>
          )}
        </div>
      )}

      {showForm && isAdmin && (
        <ManualForm
          onSaved={() => {
            setShowForm(false);
            void load();
          }}
        />
      )}

      {data && <Summary summary={data.summary} />}
      {data?.summary.last_synced_at && (
        <p className="mb-3 text-[11px] text-zinc-600">
          Последняя выгрузка: {formatDateTime(data.summary.last_synced_at)}
        </p>
      )}

      {data && data.items.length === 0 ? (
        <p className="text-xs text-zinc-600">
          Записей нет. Обновите историю из ЕИС или добавьте участия вручную.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-xs">
            <thead>
              <tr className="border-b border-white/[0.08] text-zinc-500">
                <th className="py-2 pr-3 font-medium">Закупка</th>
                <th className="py-2 pr-3 font-medium">Заказчик</th>
                <th className="py-2 pr-3 font-medium">Итог</th>
                <th className="py-2 pr-3 font-medium">Наша ставка</th>
                <th className="py-2 pr-3 font-medium">Дата</th>
                <th className="py-2 pr-3 font-medium">Источник</th>
                {isAdmin && <th className="py-2" />}
              </tr>
            </thead>
            <tbody>
              {data?.items.map((item) => (
                <tr key={item.id} className="border-b border-white/[0.04] align-top">
                  <td className="py-2 pr-3 text-zinc-300">
                    {item.tender_title ?? item.external_tender_id ?? "—"}
                    {item.external_tender_id && item.tender_title && (
                      <div className="text-[11px] text-zinc-600">{item.external_tender_id}</div>
                    )}
                    {item.lessons_learned_md && (
                      <div className="mt-1 text-[11px] text-zinc-500">
                        {item.lessons_learned_md}
                      </div>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-zinc-400">{item.customer_name ?? "—"}</td>
                  <td className="py-2 pr-3">
                    <span
                      className={`inline-block rounded-md border px-2 py-0.5 text-[11px] ${OUTCOME_CLASS[item.outcome]}`}
                    >
                      {item.outcome_label}
                    </span>
                  </td>
                  <td className="py-2 pr-3 text-zinc-400">
                    {formatPrice(item.our_bid, "RUB") ?? "—"}
                  </td>
                  <td className="py-2 pr-3 text-zinc-400">
                    {formatDate(item.executed_at) ?? "—"}
                  </td>
                  <td className="py-2 pr-3 text-zinc-600">{item.source_label}</td>
                  {isAdmin && (
                    <td className="py-2">
                      <button
                        onClick={() => void remove(item.id)}
                        className="rounded-lg border border-white/10 px-2 py-1 text-zinc-500 hover:bg-white/5 hover:text-red-400"
                        title="Удалить запись"
                      >
                        <Trash2 size={13} />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
