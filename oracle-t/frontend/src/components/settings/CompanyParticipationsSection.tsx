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
import { Tooltip } from "../ui/Tooltip";
import { SettingsNotice } from "./ui";

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
  "mt-1.5 w-full rounded-lg border border-white/10 bg-black/20 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none";

const OUTCOME_OPTIONS: Array<{ value: ParticipationOutcome; label: string }> = [
  { value: "won", label: "Победа" },
  { value: "lost", label: "Проигрыш" },
  { value: "disqualified", label: "Дисквалификация" },
  { value: "unknown", label: "Исход неизвестен" },
];

const OUTCOME_DOT: Record<ParticipationOutcome, string> = {
  won: "bg-emerald-400",
  lost: "bg-red-400",
  disqualified: "bg-amber-400",
  unknown: "bg-zinc-600",
};

const primaryButton =
  "flex items-center gap-2 rounded-lg bg-indigo-500 px-3.5 py-2 text-sm font-medium text-snow hover:bg-indigo-400 disabled:opacity-50";

const secondaryButton =
  "flex items-center gap-2 rounded-lg border border-white/10 px-3.5 py-2 text-sm text-zinc-300 transition-colors hover:bg-white/5 hover:text-zinc-100 disabled:opacity-50";

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

/**
 * Сводка — одна карточка с рядом цифр (редизайн 24.10.2026): прежние пять плиток в своих
 * цветах и абзац предупреждения под ними спорили с таблицей за внимание. Цвет исхода остался
 * только точкой у подписи, предупреждение о выборке «одни победы» — одной строкой с
 * подробностями по наведению.
 */
function Summary({ summary }: { summary: ParticipationSummary }) {
  const decided = summary.won + summary.lost + summary.disqualified;
  const winRate = summary.win_rate === null ? null : Math.round(Number(summary.win_rate));
  const figures: Array<{ label: string; value: string; dot?: string; hint?: string }> = [
    { label: "Участий", value: String(summary.total) },
    { label: "Побед", value: String(summary.won), dot: OUTCOME_DOT.won },
    { label: "Проигрышей", value: String(summary.lost), dot: OUTCOME_DOT.lost },
    {
      label: "Дисквалификаций",
      value: String(summary.disqualified),
      dot: OUTCOME_DOT.disqualified,
      hint: "не допустили к торгам",
    },
    {
      label: "Win-rate",
      value: winRate === null ? "—" : `${winRate}%`,
      hint:
        winRate === null
          ? "нет исходов"
          : summary.wins_only_data
            ? "выборка без проигрышей"
            : `от ${decided} ${plural(decided, "участия", "участий", "участий")}`,
    },
  ];
  return (
    <div className="overflow-hidden rounded-2xl border border-white/[0.08] bg-white/[0.03]">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
        {figures.map((figure, index) => (
          <div
            key={figure.label}
            className={`px-5 py-4 ${index > 0 ? "lg:border-l lg:border-white/[0.06]" : ""}`}
          >
            <div className="flex items-center gap-1.5 text-xs text-zinc-500">
              {figure.dot && <span className={`h-1.5 w-1.5 rounded-full ${figure.dot}`} />}
              {figure.label}
            </div>
            <div className="mt-1 text-2xl font-semibold tabular-nums tracking-tight text-zinc-100">
              {figure.value}
            </div>
            {figure.hint && <div className="mt-0.5 truncate text-xs text-zinc-600">{figure.hint}</div>}
          </div>
        ))}
      </div>
      {summary.wins_only_data && (
        <div className="flex items-center gap-2 border-t border-white/[0.06] px-5 py-3 text-sm text-zinc-400">
          <AlertTriangle size={14} className="shrink-0 text-amber-400" />
          <span>В истории только победы из реестра ЕИС — win-rate не показателен.</span>
          <Tooltip content="В реестре контрактов ЕИС лежат только заключённые контракты, проигрышей там нет по устройству. Измерение «История» по таким данным долю побед не считает. Проигрыши подтянутся по закупкам с поданной заявкой, а полностью — кнопкой «Обновить из rusprofile» у компании.">
            <span className="cursor-help text-zinc-600 underline decoration-dotted underline-offset-2">
              Почему
            </span>
          </Tooltip>
        </div>
      )}
    </div>
  );
}

function ManualForm({ onSaved, onCancel }: { onSaved: () => void; onCancel: () => void }) {
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
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5">
      <div className="mb-4 flex items-center gap-2">
        <h3 className="text-[15px] font-semibold text-zinc-100">Новое участие</h3>
        <Tooltip content="Для закупок, которые не подхватились автоматически: снятия с торгов, закупки вне ЕИС, всё, что было до системы. Повторная выгрузка такие записи не трогает.">
          <span className="cursor-help text-sm text-zinc-600">?</span>
        </Tooltip>
      </div>
      {error && (
        <div className="mb-4">
          <SettingsNotice tone="error">{error}</SettingsNotice>
        </div>
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <label className="block text-sm text-zinc-400 lg:col-span-2">
          Закупка
          <input
            value={form.tender_title ?? ""}
            onChange={(e) => patch({ tender_title: e.target.value })}
            placeholder="Поставка приборов учёта электроэнергии"
            className={inputClass}
          />
        </label>
        <label className="block text-sm text-zinc-400">
          Номер закупки
          <input
            value={form.external_tender_id ?? ""}
            onChange={(e) => patch({ external_tender_id: e.target.value })}
            placeholder="0158300012325000123"
            className={inputClass}
          />
        </label>
        <label className="block text-sm text-zinc-400">
          Заказчик (юрлицо)
          <input
            value={form.customer_name ?? ""}
            onChange={(e) => patch({ customer_name: e.target.value })}
            className={inputClass}
          />
        </label>
        <label className="block text-sm text-zinc-400">
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
        <label className="block text-sm text-zinc-400">
          Дата
          <input
            type="date"
            value={form.executed_at ?? ""}
            onChange={(e) => patch({ executed_at: e.target.value })}
            className={inputClass}
          />
        </label>
        <label className="block text-sm text-zinc-400">
          Наша ставка, ₽
          <input
            value={form.our_bid ?? ""}
            onChange={(e) => patch({ our_bid: e.target.value })}
            placeholder="1250000"
            className={inputClass}
          />
        </label>
        <label className="block text-sm text-zinc-400">
          Цена контракта, ₽
          <input
            value={form.final_contract_value ?? ""}
            onChange={(e) => patch({ final_contract_value: e.target.value })}
            className={inputClass}
          />
        </label>
        <label className="block text-sm text-zinc-400 lg:col-span-3">
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
      <div className="mt-4 flex justify-end gap-2">
        <button onClick={onCancel} className={secondaryButton}>
          Отмена
        </button>
        <button onClick={() => void save()} disabled={isSaving} className={primaryButton}>
          {isSaving && <Loader2 size={14} className="animate-spin" />}
          Добавить
        </button>
      </div>
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
        `Побед из ЕИС: добавлено ${result.created}, обновлено ${result.updated}. ` +
          `Проигрышей записано ${result.losses_found} из ${result.checked_submitted} поданных заявок.`,
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
    <div className="space-y-6">
      {(error || notice) && (
        <div className="space-y-2">
          {error && <SettingsNotice tone="error">{error}</SettingsNotice>}
          {notice && <SettingsNotice tone="success">{notice}</SettingsNotice>}
        </div>
      )}

      {data && <Summary summary={data.summary} />}

      {showForm && isAdmin && (
        <ManualForm
          onCancel={() => setShowForm(false)}
          onSaved={() => {
            setShowForm(false);
            void load();
          }}
        />
      )}

      <section>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3 px-1">
          <div className="text-sm text-zinc-500">
            {data?.summary.last_synced_at
              ? `Выгрузка из ЕИС: ${formatDateTime(data.summary.last_synced_at)}`
              : "Из ЕИС ещё не выгружалось"}
          </div>
          {isAdmin && (
            <div className="flex items-center gap-2">
              {!showForm && (
                <button onClick={() => setShowForm(true)} className={secondaryButton}>
                  <Plus size={15} />
                  Вручную
                </button>
              )}
              <Tooltip
                content={
                  hasInn
                    ? "Победы — из реестра контрактов по ИНН, проигрыши — по закупкам с поданной заявкой"
                    : "Укажите ИНН основной компании — реестр ищется по нему"
                }
              >
                <button
                  onClick={() => void sync()}
                  disabled={isSyncing || !hasInn}
                  className={primaryButton}
                >
                  {isSyncing ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <RefreshCw size={14} />
                  )}
                  Обновить из ЕИС
                </button>
              </Tooltip>
            </div>
          )}
        </div>

        {data && data.items.length === 0 ? (
          <p className="rounded-2xl border border-dashed border-white/[0.1] px-6 py-12 text-center text-sm text-zinc-500">
            Записей нет
          </p>
        ) : (
          <div className="overflow-x-auto rounded-2xl border border-white/[0.08] bg-white/[0.03]">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead>
                <tr className="border-b border-white/[0.06] text-xs text-zinc-500">
                  <th className="px-5 py-3 font-medium">Закупка</th>
                  <th className="px-3 py-3 font-medium">Заказчик</th>
                  <th className="px-3 py-3 font-medium">Итог</th>
                  <th className="px-3 py-3 text-right font-medium">Наша ставка</th>
                  <th className="px-3 py-3 text-right font-medium">Дата</th>
                  {isAdmin && <th className="w-12 px-3 py-3" />}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {data?.items.map((item) => (
                  <tr key={item.id} className="group align-top hover:bg-white/[0.02]">
                    <td className="max-w-[360px] px-5 py-3">
                      <div className="line-clamp-2 text-zinc-200">
                        {item.tender_title ?? item.external_tender_id ?? "—"}
                      </div>
                      <div className="mt-0.5 truncate text-xs text-zinc-600">
                        {[item.tender_title ? item.external_tender_id : null, item.source_label]
                          .filter(Boolean)
                          .join(" · ")}
                      </div>
                      {item.lessons_learned_md && (
                        <div className="mt-1 text-xs text-zinc-500">{item.lessons_learned_md}</div>
                      )}
                    </td>
                    <td className="max-w-[240px] px-3 py-3 text-zinc-400">
                      <span className="line-clamp-2">{item.customer_name ?? "—"}</span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-3">
                      <span className="inline-flex items-center gap-1.5 text-zinc-300">
                        <span className={`h-1.5 w-1.5 rounded-full ${OUTCOME_DOT[item.outcome]}`} />
                        {item.outcome_label}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-right tabular-nums text-zinc-300">
                      {formatPrice(item.our_bid, "RUB") ?? "—"}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-right tabular-nums text-zinc-400">
                      {formatDate(item.executed_at) ?? "—"}
                    </td>
                    {isAdmin && (
                      <td className="px-3 py-2.5">
                        <button
                          onClick={() => void remove(item.id)}
                          className="rounded-lg p-1.5 text-zinc-600 opacity-0 transition-opacity hover:bg-white/5 hover:text-red-400 focus:opacity-100 group-hover:opacity-100"
                          title="Удалить запись"
                        >
                          <Trash2 size={14} />
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
