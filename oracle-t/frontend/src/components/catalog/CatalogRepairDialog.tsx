import { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { CatalogRepair } from "../../api/types";

// Приведение сохранённых характеристик к единому формату (замечания тендерного отдела
// 13.10.2026; до 14.10.2026 — кнопка «Навести порядок в характеристиках» в списке
// производителей, теперь пункт меню «⋯» над ним и это окно). Новые правила — класс точности «x/x», фазы только 1 или 3, вариант своей
// модели вместо перечня всего семейства, без кодов из обозначения, приоритет источников —
// действуют для всего, что записывается теперь; то, что накопилось раньше, исправляет эта
// кнопка. Сначала предпросмотр (база не меняется), потом подтверждение. Модель ИИ не
// вызывается: значения правятся по правилам, удалённое из «Описания типа» перекрывается
// повторным обходом сайтов — тоже без ИИ.

const COUNTS: [keyof CatalogRepair, string][] = [
  ["values_normalised", "приведено к формату (класс точности, ток, фазы)"],
  ["family_values_resolved", "выбран вариант своей модели из перечня семейства"],
  ["values_removed", "удалено неверных значений (коды обозначения, «2 фазы», перечни без варианта)"],
  ["modifications_cleared", "снято чужих обозначений исполнения"],
  ["extra_fields_spread", "из «Вне справочника» разложено по полям"],
  ["extra_entries_removed", "убрано дублей из «Вне справочника»"],
];

function RepairSummary({ result }: { result: CatalogRepair }) {
  const rows = COUNTS.filter(([key]) => (result[key] as number) > 0);
  return (
    <div className="space-y-2 text-xs">
      <p className="text-zinc-300">
        {result.applied ? "Исправлено" : "Будет исправлено"} моделей:{" "}
        <span className="font-semibold tabular-nums text-zinc-50">{result.products_affected}</span>
      </p>
      {rows.length > 0 && (
        <ul className="space-y-0.5">
          {rows.map(([key, label]) => (
            <li key={key} className="flex gap-2 text-zinc-400">
              <span className="w-10 shrink-0 text-right font-mono tabular-nums text-zinc-200">{result[key] as number}</span>
              {label}
            </li>
          ))}
        </ul>
      )}
      {result.manufacturers_to_refresh.length > 0 && (
        <p className="text-zinc-500">
          {result.applied ? "Сайты обходятся заново в фоне" : "После применения сайты будут обойдены заново"} (без ИИ):{" "}
          {result.manufacturers_to_refresh.join(", ")}.
        </p>
      )}
      {result.examples.length > 0 && (
        <details className="text-zinc-500">
          <summary className="cursor-pointer text-zinc-400 hover:text-zinc-200">Примеры изменений</summary>
          <ul className="mt-1 max-h-48 space-y-0.5 overflow-y-auto font-mono text-[11px]">
            {result.examples.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function CatalogRepairDialog({ onClose }: { onClose: () => void }) {
  const [result, setResult] = useState<CatalogRepair | null>(null);
  const [busy, setBusy] = useState<"preview" | "apply" | null>("preview");
  const [error, setError] = useState<string | null>(null);

  const call = async (mode: "preview" | "apply") => {
    setBusy(mode);
    setError(null);
    try {
      setResult(
        mode === "preview"
          ? await api.get<CatalogRepair>("/catalog/repair-characteristics")
          : await api.post<CatalogRepair>("/catalog/repair-characteristics")
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось выполнить");
    } finally {
      setBusy(null);
    }
  };

  // Окно открывается сразу с предпросмотром: база не меняется, пока не нажато «Применить».
  useEffect(() => {
    void call("preview");
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="dialog" aria-modal="true" aria-label="Единый формат значений">
      <button aria-label="Закрыть" onClick={onClose} className="absolute inset-0 cursor-default bg-black/60" />
      <div className="relative w-full max-w-lg rounded-xl border border-white/[0.08] bg-zinc-950 p-5 shadow-[0_20px_60px_rgba(0,0,0,.5)]">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-zinc-50">
              {result?.applied ? "Готово" : "Привести значения к единому формату"}
            </h3>
            <p className="mt-0.5 text-xs text-zinc-500">
              Класс точности, токи, фазы, обозначения — по правилам, без ИИ. Ручные и проверенные значения не меняются.
            </p>
          </div>
          <button onClick={onClose} aria-label="Закрыть" className="rounded-lg p-1 text-zinc-400 hover:bg-white/5 hover:text-zinc-100">
            <X size={16} />
          </button>
        </div>
        {!result ? (
          busy === "preview" ? (
            <p className="flex items-center gap-2 text-xs text-zinc-400">
              <Loader2 size={12} className="animate-spin" /> Проверяю сохранённые значения…
            </p>
          ) : null
        ) : result.products_affected === 0 ? (
          <p className="text-xs text-zinc-400">Менять нечего — значения уже в едином формате.</p>
        ) : (
          <RepairSummary result={result} />
        )}
        {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
        <div className="mt-4 flex justify-end gap-2">
          {result && !result.applied && result.products_affected > 0 ? (
            <>
              <button onClick={onClose} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/5">
                Отмена
              </button>
              <button
                onClick={() => void call("apply")}
                disabled={busy !== null}
                className="flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
              >
                {busy === "apply" && <Loader2 size={12} className="animate-spin" />}
                Применить
              </button>
            </>
          ) : (
            <button onClick={onClose} className="rounded-lg border border-white/10 px-3 py-1.5 text-xs text-zinc-300 hover:bg-white/5">
              Закрыть
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
