import { useMemo } from "react";

import { Info } from "lucide-react";

import type { Product, ProductMounting, SiType } from "../../api/types";
import { productDisplayName } from "../../utils/format";
import { Tooltip } from "../ui/Tooltip";

// Модели производителя, разложенные по исполнениям: строки — фазность, столбцы — способ
// установки (предложение тестировщика 18.09.2026). Сплошной список из двух сотен позиций
// Энергомеры не отвечал на вопрос «какие у них есть трёхфазные сплиты» — матрица отвечает.
//
// Модель с универсальным корпусом (DIN-рейка + винты) стоит в каждом подходящем столбце:
// продавцу важно, что прибор подходит под щит, а не то, что он «в первую очередь» DIN.
// Снятые с производства уходят в конец ячейки и приглушены: они интересны только когда
// заказчик всё ещё указывает их в документации.
//
// 14.10.2026 (редизайн каталога): у плашки модели — только название и номер ГРСИ. Пометки
// «из реестра ФГИС», «снят с производства», «⚠ проверить» разными цветами превращали
// матрицу в пёструю ленту; снятые и так стоят отдельным списком в конце ячейки, «нужно
// проверить» — янтарная точка с причиной в подсказке, происхождение — в подсказке названия.

type ColumnKey = ProductMounting | "unknown";
// Строки — фазность и способ включения, как в одиннадцати типах файла «Параметры для ПУ»
// (25.09.2026): «трёхфазные» одной строкой смешивали прямое включение с трансформаторным,
// а это разные закупки. Модель без определённого включения остаётся в строке по фазности.
type RowKey =
  | "1"
  | "3_direct"
  | "3_semi"
  | "3_indirect"
  | "3"
  | "hv"
  | "unknown";

const COLUMNS: { key: ColumnKey; label: string; hint: string }[] = [
  { key: "split", label: "Сплит", hint: "наружная установка, на опору" },
  { key: "din", label: "DIN-рейка", hint: "рейка ТН35, компактный корпус" },
  { key: "panel", label: "Щит / шкаф", hint: "крепление на винты, щитовой корпус" },
  { key: "unknown", label: "Не определено", hint: "нет характеристик «Тип монтажа» / «Тип корпуса»" },
];

const ROWS: { key: RowKey; label: string }[] = [
  { key: "1", label: "Однофазные" },
  { key: "3_direct", label: "Трёхфазные прямого включения" },
  { key: "3_semi", label: "Трёхфазные полукосвенного включения" },
  { key: "3_indirect", label: "Трёхфазные косвенного включения" },
  { key: "3", label: "Трёхфазные, включение не определено" },
  { key: "hv", label: "Высоковольтные (ВПУ)" },
  { key: "unknown", label: "Фазность не определена" },
];

function rowsOf(p: Product): RowKey[] {
  const rows = new Set<RowKey>();
  for (const kind of p.meter_kinds ?? []) {
    if (kind === "hv") rows.add("hv");
    else if (kind.startsWith("1ph")) rows.add("1");
    else if (kind.startsWith("3ph_direct")) rows.add("3_direct");
    else if (kind.startsWith("3ph_semi")) rows.add("3_semi");
    else if (kind.startsWith("3ph_indirect")) rows.add("3_indirect");
  }
  if (rows.size) return [...rows];
  return [p.phases === 1 ? "1" : p.phases === 3 ? "3" : "unknown"];
}

function columnsOf(p: Product): ColumnKey[] {
  return p.mountings.length ? p.mountings : ["unknown"];
}

function byName(a: Product, b: Product) {
  return productDisplayName(a.model_name).localeCompare(productDisplayName(b.model_name), "ru");
}

export function ProductMatrix({
  products,
  siTypes,
  selectedProductId,
  onSelect,
}: {
  products: Product[];
  siTypes: SiType[];
  selectedProductId: string | null;
  onSelect: (productId: string) => void;
}) {
  const siCodeById = useMemo(() => new Map(siTypes.map((s) => [s.id, s.si_code])), [siTypes]);

  const { cells, columns, rows } = useMemo(() => {
    const cells = new Map<string, Product[]>();
    const usedColumns = new Set<ColumnKey>();
    const usedRows = new Set<RowKey>();
    for (const p of products) {
      for (const row of rowsOf(p)) {
        usedRows.add(row);
        for (const col of columnsOf(p)) {
          usedColumns.add(col);
          const key = `${row}:${col}`;
          (cells.get(key) ?? cells.set(key, []).get(key)!).push(p);
        }
      }
    }
    return {
      cells,
      columns: COLUMNS.filter((c) => usedColumns.has(c.key)),
      rows: ROWS.filter((r) => usedRows.has(r.key)),
    };
  }, [products]);

  const columnCount = (col: ColumnKey) =>
    new Set(rows.flatMap((r) => (cells.get(`${r.key}:${col}`) ?? []).map((p) => p.id))).size;

  const renderChip = (p: Product) => {
    const discontinued = p.status === "discontinued";
    const siCode = p.si_type_id ? siCodeById.get(p.si_type_id) ?? null : null;
    const needsReview = p.review_status === "needs_review";
    const origin = [
      p.model_name !== productDisplayName(p.model_name) ? p.model_name : null,
      p.execution ? `Исполнение: ${p.execution}` : null,
      p.data_source === "fgis" ? `Заведена из реестра ФГИС${p.registry_modification ? `: ${p.registry_modification}` : ""}` : null,
      needsReview ? `Проверить: ${p.review_reason ?? "нужна проверка"}` : null,
    ]
      .filter(Boolean)
      .join("\n");
    return (
      <Tooltip key={p.id} content={origin} className="w-full">
        <button
          onClick={() => onSelect(p.id)}
          className={`flex w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left text-xs transition-colors ${
            p.id === selectedProductId
              ? "border-indigo-500/40 bg-indigo-500/10"
              : "border-white/[0.08] hover:border-white/15 hover:bg-white/[0.04]"
          }`}
        >
          <span className="min-w-0 flex-1">
            <span className={`block truncate ${discontinued ? "text-zinc-500" : "text-zinc-200"}`}>
              {productDisplayName(p.model_name)}
            </span>
            {/* Регистрационный номер в Госреестре СИ; «без кода СИ» — пометка, а не пустое
                место (вопрос тестировщика 18.09.2026). */}
            <span className="block font-mono text-[10px] text-zinc-500">{siCode ?? "без кода СИ"}</span>
          </span>
          {needsReview && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" aria-label="нужно проверить" />}
        </button>
      </Tooltip>
    );
  };

  const renderCell = (row: RowKey, col: ColumnKey) => {
    const items = cells.get(`${row}:${col}`) ?? [];
    const active = items.filter((p) => p.status !== "discontinued").sort(byName);
    const discontinued = items.filter((p) => p.status === "discontinued").sort(byName);
    if (!items.length) return <span className="text-[11px] text-zinc-600">—</span>;
    return (
      <div className="flex flex-col gap-1.5">
        {active.map(renderChip)}
        {discontinued.length > 0 && (
          <>
            <div className="mt-2 text-[11px] text-zinc-600">Сняты с производства · {discontinued.length}</div>
            {discontinued.map(renderChip)}
          </>
        )}
      </div>
    );
  };

  return (
    <div className="overflow-x-auto">
      <div
        className="grid"
        style={{ gridTemplateColumns: `9rem repeat(${columns.length}, minmax(12rem, 1fr))` }}
      >
        <div className="flex items-end border-b border-white/[0.08] px-3 py-2">
          <Tooltip
            content={
              "Исполнение выведено из характеристик «Количество фаз», «Тип монтажа», «Тип корпуса» и обозначения модели; " +
              "универсальный корпус (DIN-рейка + винты) показан в обоих столбцах.\n" +
              "Включение — по напряжению и токам: 230 В и 60–100 А — прямое, 230 В и 10 А — полукосвенное, " +
              "57,7 В — косвенное, 6–10 кВ — ВПУ."
            }
          >
            <Info size={13} className="text-zinc-600 hover:text-zinc-300" aria-label="Как разложены модели" />
          </Tooltip>
        </div>
        {columns.map((c) => (
          <div key={c.key} className="border-b border-l border-white/[0.08] px-3 py-2">
            <Tooltip content={c.hint}>
              <span className="text-xs font-medium text-zinc-200">
                {c.label} <span className="font-normal tabular-nums text-zinc-500">{columnCount(c.key)}</span>
              </span>
            </Tooltip>
          </div>
        ))}
        {rows.map((r) => (
          <div key={r.key} className="contents">
            <div className="border-b border-white/[0.06] px-3 py-3 text-xs text-zinc-400">{r.label}</div>
            {columns.map((c) => (
              <div key={c.key} className="border-b border-l border-white/[0.06] px-3 py-3 align-top">
                {renderCell(r.key, c.key)}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
