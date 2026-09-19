import { useMemo } from "react";

import type { Product, ProductMounting, SiType } from "../../api/types";

// Модели производителя, разложенные по исполнениям: строки — фазность, столбцы — способ
// установки (предложение тестировщика 18.09.2026). Сплошной список из двух сотен позиций
// Энергомеры не отвечал на вопрос «какие у них есть трёхфазные сплиты» — матрица отвечает.
//
// Модель с универсальным корпусом (DIN-рейка + винты) стоит в каждом подходящем столбце:
// продавцу важно, что прибор подходит под щит, а не то, что он «в первую очередь» DIN.
// Снятые с производства уходят в конец ячейки и приглушены: они интересны только когда
// заказчик всё ещё указывает их в документации.

type ColumnKey = ProductMounting | "unknown";
type RowKey = "1" | "3" | "unknown";

const COLUMNS: { key: ColumnKey; label: string; hint: string }[] = [
  { key: "split", label: "Сплит", hint: "наружная установка, на опору" },
  { key: "din", label: "DIN-рейка", hint: "рейка ТН35, компактный корпус" },
  { key: "panel", label: "Щит / шкаф", hint: "крепление на винты, щитовой корпус" },
  { key: "unknown", label: "Установка не определена", hint: "нет характеристик «Тип монтажа» / «Тип корпуса»" },
];

const ROWS: { key: RowKey; label: string }[] = [
  { key: "1", label: "Однофазные" },
  { key: "3", label: "Трёхфазные" },
  { key: "unknown", label: "Фазность не определена" },
];

function rowOf(p: Product): RowKey {
  return p.phases === 1 ? "1" : p.phases === 3 ? "3" : "unknown";
}

function columnsOf(p: Product): ColumnKey[] {
  return p.mountings.length ? p.mountings : ["unknown"];
}

function byName(a: Product, b: Product) {
  return a.model_name.localeCompare(b.model_name, "ru");
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
      const row = rowOf(p);
      usedRows.add(row);
      for (const col of columnsOf(p)) {
        usedColumns.add(col);
        const key = `${row}:${col}`;
        (cells.get(key) ?? cells.set(key, []).get(key)!).push(p);
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
    return (
      <button
        key={p.id}
        onClick={() => onSelect(p.id)}
        className={`flex w-full flex-col gap-0.5 rounded-lg border px-2.5 py-1.5 text-left text-xs ${
          p.id === selectedProductId
            ? "border-indigo-500/40 bg-indigo-500/10 text-indigo-300"
            : discontinued
              ? "border-white/5 text-zinc-500 hover:bg-white/5"
              : "border-white/10 text-zinc-300 hover:bg-white/5"
        }`}
      >
        <span className="truncate" title={p.model_name}>
          {p.model_name}
          {p.execution && <span className="ml-1.5 text-[10px] text-zinc-500">{p.execution}</span>}
        </span>
        <span className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[10px]">
          {/* Регистрационный номер в Госреестре СИ — у каждой модели, у которой он найден;
              «без кода СИ» — отдельная пометка, а не пустое место (вопрос тестировщика
              18.09.2026). */}
          {siCode ? (
            <span className="font-mono text-emerald-400/80" title="Регистрационный номер в Госреестре СИ">
              ГРСИ {siCode}
            </span>
          ) : (
            <span className="text-zinc-500">без кода СИ</span>
          )}
          {discontinued && <span className="text-amber-400/70">снят с производства</span>}
          {p.data_source === "fgis" && (
            <span className="text-sky-400" title={p.registry_modification ?? undefined}>
              из реестра ФГИС
            </span>
          )}
          {p.review_status === "needs_review" && (
            <span className="text-amber-400" title={p.review_reason ?? undefined}>
              ⚠ проверить
            </span>
          )}
        </span>
      </button>
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
            <div className="mt-1 text-[10px] uppercase tracking-wide text-zinc-600">
              снято с производства · {discontinued.length}
            </div>
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
        style={{ gridTemplateColumns: `7rem repeat(${columns.length}, minmax(12rem, 1fr))` }}
      >
        <div className="border-b border-white/[0.08] px-3 py-2" />
        {columns.map((c) => (
          <div key={c.key} className="border-b border-l border-white/[0.08] px-3 py-2">
            <div className="text-xs font-semibold text-zinc-200">
              {c.label} <span className="font-normal text-zinc-500">· {columnCount(c.key)}</span>
            </div>
            <div className="text-[10px] text-zinc-500">{c.hint}</div>
          </div>
        ))}
        {rows.map((r) => (
          <div key={r.key} className="contents">
            <div className="border-b border-white/[0.06] px-3 py-3 text-xs font-medium text-zinc-300">{r.label}</div>
            {columns.map((c) => (
              <div key={c.key} className="border-b border-l border-white/[0.06] px-3 py-3 align-top">
                {renderCell(r.key, c.key)}
              </div>
            ))}
          </div>
        ))}
      </div>
      <p className="px-1 pt-2 text-[10px] text-zinc-500">
        Исполнение выведено из характеристик «Количество фаз», «Тип монтажа», «Тип корпуса» и обозначения
        модели; универсальный корпус (DIN-рейка + винты) показан в обоих столбцах.
      </p>
    </div>
  );
}
