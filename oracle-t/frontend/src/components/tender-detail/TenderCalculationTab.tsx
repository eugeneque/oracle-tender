import { Loader2 } from "lucide-react";

import type { NicheStatistics } from "../../api/types";
import { formatDateTime } from "../../utils/format";

/**
 * Вкладка «Расчёт» (раздел 5.6 ТЗ): ценовая аналитика и статистика конкуренции за нишу
 * (ОКПД2 + регион).
 *
 * Пока агрегаты по нише не собраны, вкладка честно сообщает, что анализ не запускался, —
 * а не рисует нули. Нулевая медиана снижения цены выглядела бы как измеренный факт, тогда
 * как её никто не измерял (недочёт финансового блока с созвона 02.09.2026).
 */

function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: string | null;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className="mt-1 text-lg font-semibold text-zinc-100">{value ?? "—"}</div>
      {hint && <div className="mt-0.5 text-[11px] text-zinc-600">{hint}</div>}
    </div>
  );
}

function percent(value: string | null): string | null {
  if (value === null) return null;
  const parsed = Number(value);
  return Number.isNaN(parsed) ? null : `${parsed.toFixed(1)}%`;
}

export function TenderCalculationTab({
  statistics,
  isLoading,
  okpd2,
}: {
  statistics: NicheStatistics | null;
  isLoading: boolean;
  okpd2: string | null;
}) {
  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-zinc-500">
        <Loader2 size={14} className="animate-spin" />
        Загрузка статистики по нише…
      </div>
    );
  }

  if (!okpd2) {
    return (
      <div className="rounded-xl border border-dashed border-white/10 p-4 text-sm text-zinc-500">
        У тендера не определён код ОКПД2 — по нему и подбирается статистика ниши. Запустите
        анализ документации или укажите код вручную на вкладке «Основное».
      </div>
    );
  }

  if (!statistics) {
    return (
      <div className="rounded-xl border border-dashed border-white/10 p-4 text-sm text-zinc-500">
        Анализ ниши {okpd2} ещё не запускался: агрегированных данных по этой группе закупок в
        системе пока нет. Показывать нули здесь было бы враньём — цифры появятся, когда
        соберутся исходы закупок по нише.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <Metric
          label="Обычное число участников"
          value={statistics.avg_participants ? Number(statistics.avg_participants).toFixed(1) : null}
        />
        <Metric
          label="Закупок с одним участником"
          value={percent(statistics.single_participant_share)}
        />
        <Metric
          label="Медианное снижение цены"
          value={percent(statistics.median_price_reduction_pct)}
        />
        <Metric
          label="Обычный срок подачи"
          value={statistics.usual_submission_days ? `${statistics.usual_submission_days} дн.` : null}
        />
      </div>

      <section>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">
          Кто выигрывает такие закупки
        </h4>
        {statistics.top_winners.length === 0 ? (
          <p className="text-xs text-zinc-600">Данных о победителях в этой нише пока нет.</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-white/[0.08]">
            <table className="w-full text-sm">
              <thead className="bg-white/[0.03] text-xs text-zinc-500">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Компания</th>
                  <th className="px-3 py-2 text-right font-medium">Побед</th>
                  <th className="px-3 py-2 text-right font-medium">Доля побед</th>
                  <th className="px-3 py-2 text-right font-medium">Типичный дисконт</th>
                </tr>
              </thead>
              <tbody>
                {statistics.top_winners.map((winner, index) => (
                  <tr key={index} className="border-t border-white/[0.06]">
                    <td className="px-3 py-2 text-zinc-200">{winner.manufacturer_name}</td>
                    <td className="px-3 py-2 text-right text-zinc-400">{winner.wins_count ?? "—"}</td>
                    <td className="px-3 py-2 text-right text-zinc-400">
                      {winner.win_share_pct !== undefined ? `${winner.win_share_pct}%` : "—"}
                    </td>
                    <td className="px-3 py-2 text-right text-zinc-400">
                      {winner.typical_discount_pct !== undefined
                        ? `${winner.typical_discount_pct}%`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="text-[11px] text-zinc-600">
        Ниша: ОКПД2 {statistics.okpd2_code}
        {statistics.region_code ? `, регион ${statistics.region_code}` : ", по всей стране"}. В
        основе — {statistics.sample_size ?? "?"} закупок. Обновлено:{" "}
        {formatDateTime(statistics.calculated_at)}.
      </p>
    </div>
  );
}
