import { ArrowDown, ArrowUp, ArrowUpDown, Star } from "lucide-react";

import type { Tender } from "../../api/types";
import { TruncatedText } from "./TruncatedText";
import {
  daysLeft,
  formatDate,
  formatPrice,
  percentTextClass,
  percentValue,
  stageLabel,
  statusLabel,
  tenderTypeLabel,
} from "../../utils/format";

/** Ключи совпадают с именами полей в API (`sort_by`): сортировка выполняется запросом к
 * серверу, поэтому промежуточный словарь «ключ колонки → имя поля» был бы лишним звеном. */
export type SortKey =
  | "title"
  | "customer_name"
  | "stage"
  | "status"
  | "publish_date"
  | "application_end"
  | "price"
  | "ai_score"
  | "win_percentage";
export type SortDirection = "asc" | "desc";

/**
 * Ширины колонок заданы явно и в процентах, потому что таблица работает в режиме
 * `table-fixed`. Без него браузер растягивает колонки под самое длинное значение —
 * наименование тендера бывает в две сотни символов, — и `truncate` не срабатывает вовсе:
 * обрезать нечего, ячейка всегда шире содержимого.
 */
const COLUMNS: {
  key: SortKey;
  label: string;
  width: string;
  align?: "right";
}[] = [
  { key: "title", label: "Наименование", width: "26%" },
  { key: "customer_name", label: "Заказчик", width: "16%" },
  { key: "stage", label: "Этап", width: "11%" },
  { key: "status", label: "Статус", width: "10%" },
  { key: "publish_date", label: "Опубл.", width: "9%" },
  { key: "application_end", label: "Приём до", width: "12%" },
  { key: "price", label: "Цена", width: "8%", align: "right" },
  // Две процентные колонки рядом: AI-оценка отвечает «идти ли», процент соответствия —
  // «подходит ли прибор» (разделы 5.5.1 и 5.5.2 ТЗ). Это разные вопросы, и подменять один
  // другим нельзя.
  { key: "ai_score", label: "AI", width: "5%", align: "right" },
  { key: "win_percentage", label: "%", width: "5%", align: "right" },
];

/** Подпись под наименованием: источник, код ОКПД2 и тип конкурса одной строкой. */
function metaLine(tender: Tender): string {
  return [tender.source.name, tender.okpd2_code, tenderTypeLabel(tender.tender_type)]
    .filter(Boolean)
    .join(" · ");
}

/**
 * Вид «Таблица» — плотная сетка с сортировкой по любой колонке (раздел 5.6 ТЗ).
 *
 * Сортировка серверная, а не по загруженному массиву: выдача разбита на страницы, и
 * сортировка одной страницы дала бы неверный порядок — «самый дорогой тендер» оказался бы
 * самым дорогим среди пятидесяти показанных, а не среди всех найденных.
 */
export function TenderTableView({
  tenders,
  onOpen,
  sortKey,
  direction,
  onSortChange,
}: {
  tenders: Tender[];
  onOpen: (tender: Tender) => void;
  sortKey: SortKey;
  direction: SortDirection;
  onSortChange: (key: SortKey, direction: SortDirection) => void;
}) {
  const sorted = tenders;

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      onSortChange(key, direction === "asc" ? "desc" : "asc");
      return;
    }
    onSortChange(key, "asc");
  };

  return (
    // Горизонтальная прокрутка внутри контейнера: на узком экране таблица ужимается до
    // предела читаемости и дальше едет вбок, но страница целиком не разъезжается.
    <div className="overflow-x-auto rounded-2xl border border-white/[0.06] bg-white/[0.02]">
      <table className="w-full min-w-[820px] table-fixed border-collapse text-[13px]">
        <colgroup>
          {COLUMNS.map((column) => (
            <col key={column.key} style={{ width: column.width }} />
          ))}
        </colgroup>
        <thead>
          <tr className="border-b border-white/[0.08]">
            {COLUMNS.map((column) => {
              const isActive = column.key === sortKey;
              const Icon = !isActive ? ArrowUpDown : direction === "asc" ? ArrowUp : ArrowDown;
              return (
                <th
                  key={column.key}
                  scope="col"
                  className={`px-2.5 py-2 text-xs font-medium ${
                    column.align === "right" ? "text-right" : "text-left"
                  }`}
                >
                  <button
                    onClick={() => toggleSort(column.key)}
                    className={`inline-flex items-center gap-1 transition-colors ${
                      isActive ? "text-zinc-100" : "text-zinc-500 hover:text-zinc-300"
                    }`}
                  >
                    {column.label}
                    <Icon size={11} className="shrink-0" />
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((tender) => {
            const left = daysLeft(tender.application_end);
            const percent = percentValue(tender.win_percentage);
            const aiScore = percentValue(tender.ai_score);

            return (
              <tr
                key={tender.id}
                onClick={() => onOpen(tender)}
                className="cursor-pointer border-b border-white/[0.04] transition-colors last:border-0 hover:bg-white/[0.04]"
              >
                <td className="max-w-0 px-2.5 py-2">
                  <div className="flex items-center gap-1">
                    {tender.is_bookmarked && (
                      <Star size={12} fill="currentColor" className="shrink-0 text-amber-400" aria-label="В избранном" />
                    )}
                    <TruncatedText text={tender.title} className="min-w-0 text-zinc-100" />
                  </div>
                  <TruncatedText text={metaLine(tender)} className="mt-0.5 text-[11px] text-zinc-600" />
                </td>
                <td className="max-w-0 px-2.5 py-2">
                  <TruncatedText text={tender.customer_name} className="text-zinc-400" />
                </td>
                <td className="max-w-0 px-2.5 py-2">
                  <TruncatedText text={stageLabel(tender.stage)} className="text-zinc-300" />
                </td>
                <td className="max-w-0 px-2.5 py-2">
                  <TruncatedText text={statusLabel(tender.status)} className="text-zinc-400" />
                </td>
                <td className="whitespace-nowrap px-2.5 py-2 text-zinc-500">
                  {formatDate(tender.publish_date) ?? "—"}
                </td>
                <td className="whitespace-nowrap px-2.5 py-2">
                  <span className={left !== null && left <= 5 ? "text-red-400" : "text-zinc-400"}>
                    {formatDate(tender.application_end) ?? "—"}
                  </span>
                  {left !== null && (
                    <span className="ml-1 text-[11px] text-zinc-600">
                      {left >= 0 ? `${left}д` : "истёк"}
                    </span>
                  )}
                </td>
                <td className="whitespace-nowrap px-2.5 py-2 text-right font-medium text-zinc-200">
                  {formatPrice(tender.price, tender.currency) ?? "—"}
                </td>
                <td className="whitespace-nowrap px-2.5 py-2 text-right">
                  {aiScore === null ? (
                    <span className="text-zinc-700">—</span>
                  ) : (
                    <span className={`font-medium ${percentTextClass(aiScore)}`}>{aiScore}%</span>
                  )}
                </td>
                <td className="whitespace-nowrap px-2.5 py-2 text-right">
                  {percent === null ? (
                    <span className="text-zinc-700">—</span>
                  ) : (
                    <span
                      className={`font-medium ${
                        percent >= 80
                          ? "text-emerald-400"
                          : percent >= 50
                            ? "text-amber-400"
                            : "text-red-400"
                      }`}
                    >
                      {percent}%
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
