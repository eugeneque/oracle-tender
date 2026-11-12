import { useEffect, useState } from "react";

import { ApiError, api } from "../../api/client";
import type { AiProviderKey } from "../../api/types";
import { AiProviderIcon } from "../AiProviderIcon";

/**
 * Модель матрицы соответствия (12.11.2026, раздел 104 ARCHITECTURE.md). Матрица — около 90%
 * всех токенов ИИ, поэтому модель для неё выбирается отдельно от модели разбора: можно
 * перевести на дешёвую модель только матрицу и сравнить качество, не трогая остальное.
 * Рядом — фактический средний расход матрицы на закупку по каждой модели за 60 дней.
 */

interface MatrixModelStatus {
  provider: AiProviderKey | null;
  costs: { provider: AiProviderKey; tenders: number; tokens_per_tender: number; cost_per_tender: number | null }[];
}

const OPTIONS: { value: AiProviderKey | ""; label: string }[] = [
  { value: "", label: "Как у всего разбора (по умолчанию)" },
  { value: "qwen", label: "Qwen (RouterAI)" },
  { value: "deepseek", label: "DeepSeek (RouterAI)" },
  { value: "yandex", label: "YandexGPT" },
];
const LABELS: Record<AiProviderKey, string> = { qwen: "Qwen", deepseek: "DeepSeek", yandex: "YandexGPT" };
const numberFormat = new Intl.NumberFormat("ru-RU");

export function MatrixModelCard() {
  const [state, setState] = useState<MatrixModelStatus | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<MatrixModelStatus>("/integrations/matrix-model")
      .then(setState)
      .catch((err) => setError(err instanceof ApiError ? err.message : "Не удалось загрузить модель матрицы"));
  }, []);

  const save = async (value: string) => {
    setError(null);
    setIsSaving(true);
    try {
      setState(await api.put<MatrixModelStatus>("/integrations/matrix-model", { provider: value || null }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось сменить модель матрицы");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="rounded-2xl border border-white/[0.08] bg-white/[0.03] p-5">
      <div className="flex flex-wrap items-start gap-3">
        <span className="flex h-11 shrink-0 items-center rounded-xl bg-white/[0.05] px-2.5">
          <AiProviderIcon provider={state?.provider ?? "qwen"} size={22} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[15px] font-semibold text-zinc-100">Модель матрицы соответствия</div>
          <div className="mt-0.5 max-w-2xl text-xs leading-relaxed text-zinc-500">
            Матрица — самая дорогая часть разбора, около 90% всех токенов: каждое требование
            сверяется с каталогом каждого производителя. Здесь можно выбрать для неё отдельную модель,
            например более дешёвую, и сравнить итоги. Выбранная модель действует для всех разборов; если её выключат, матрица остановится, а не перейдёт на другую модель.
          </div>
        </div>
        <select
          className="rounded-lg border border-white/[0.08] bg-black/20 px-3 py-2 text-sm text-zinc-100"
          value={state?.provider ?? ""}
          disabled={!state || isSaving}
          onChange={(event) => void save(event.target.value)}
          aria-label="Модель матрицы соответствия"
        >
          {OPTIONS.map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </div>

      {error && (
        <div className="mt-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
          {error}
        </div>
      )}

      {state && state.costs.length > 0 && (
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {state.costs.map((item) => (
            <div key={item.provider} className="rounded-xl bg-black/20 px-4 py-3">
              <div className="text-[11px] text-zinc-500">{LABELS[item.provider]} · матрица на закупку</div>
              <div className="mt-0.5 text-lg font-semibold tabular-nums text-zinc-100">
                {item.cost_per_tender !== null
                  ? `≈ ${item.cost_per_tender.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`
                  : "цена неизвестна"}
              </div>
              <div className="text-xs text-zinc-500">
                {numberFormat.format(item.tokens_per_tender)} токенов · по {numberFormat.format(item.tenders)} закупкам за 60 дней
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
