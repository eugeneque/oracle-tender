import { useEffect, useState } from "react";
import { ExternalLink, ShieldCheck } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { TenderRegistryCheck } from "../../api/types";
import { registryStateStyle } from "../../utils/format";
import { CriticalityBadge } from "./TenderRequirementsTab";

// Проверка допусков по закупке: реестр промпродукции (ПП 719), ЗАК ПАО «Россети»,
// реестр российского ПО (замечание тестировщика 16.09.2026).
//
// Тот же принцип, что и у блока интеграции в ПО: требование о допуске — формальный
// критерий отклонения заявки, и ответ на него должен быть виден над матрицей, а не в её
// пятидесяти строках. Показываются только упомянутые в ТЗ реестры; у каждого
// производителя — лучшее состояние по его моделям и сами модели в подсказке.
//
// Серое «не проверялось» — не приговор: запись просто не заведена. Занести её можно в
// каталоге, в карточке модели, — ссылка на реестр там же.

const STATE_SHORT: Record<string, string> = {
  active: "✓",
  expiring: "!",
  expired: "✕",
  absent: "✕",
  unknown: "—",
};

export function TenderRegistryBlock({ tenderId }: { tenderId: string }) {
  const [data, setData] = useState<TenderRegistryCheck | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    api
      .get<TenderRegistryCheck>(`/tenders/${tenderId}/registry-check`)
      .then(setData)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить проверку допусков")
      );
  }, [tenderId]);

  if (error) {
    return (
      <div className="mb-4 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">
        {error}
      </div>
    );
  }
  if (data === null || data.requirements.length === 0) return null;

  const registries = data.registries.filter((r) => r.mentioned);
  const labelOf = (key: string) => data.registries.find((r) => r.key === key)?.label ?? key;

  return (
    <div className="mb-4 rounded-xl border border-white/[0.08] bg-white/[0.02]">
      <div className="border-b border-white/[0.06] px-3 py-2.5">
        <div className="flex items-center gap-1.5 text-sm font-medium text-zinc-200">
          <ShieldCheck size={14} className="text-indigo-400" />
          Допуски: реестры и аттестация
        </div>
        <ul className="mt-1.5 space-y-1">
          {data.requirements.map((requirement) => (
            <li key={requirement.id} className="text-[12px] leading-snug text-zinc-400">
              <span className="text-zinc-300">{requirement.text}</span>
              {requirement.criticality && <CriticalityBadge value={requirement.criticality} />}
              <span className="text-indigo-400"> → {requirement.registries.map(labelOf).join(", ")}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-white/[0.06]">
              <th className="px-3 py-2 text-left text-xs font-medium text-zinc-500">Производитель</th>
              {registries.map((registry) => (
                <th
                  key={registry.key}
                  className="px-1.5 py-2 text-center text-[11px] font-medium text-indigo-300"
                  title={registry.official_name}
                >
                  <span className="inline-flex items-center gap-1">
                    <span className="block max-w-[140px] truncate">{registry.label}</span>
                    {registry.url && (
                      <a href={registry.url} target="_blank" rel="noreferrer" title="Открыть реестр">
                        <ExternalLink size={10} />
                      </a>
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.manufacturers.map((row) => (
              <tr key={row.manufacturer_id} className="border-b border-white/[0.04]">
                <td className="px-3 py-1.5 text-zinc-300">
                  {row.name}
                  {row.is_mirtek && <span className="ml-1.5 text-[11px] text-indigo-400">наш</span>}
                </td>
                {registries.map((registry) => {
                  const cell = row.registries[registry.key];
                  const state = cell?.state ?? "unknown";
                  const style = registryStateStyle(state);
                  const title =
                    cell && cell.products.length > 0
                      ? cell.products.map((p) => `${p.model_name}: ${p.summary}`).join("\n")
                      : "запись по моделям не заведена — внести можно в каталоге, в карточке модели";
                  return (
                    <td key={registry.key} className="px-1.5 py-1.5 text-center">
                      <span
                        title={`${style.label}\n${title}`}
                        className={`inline-flex h-6 min-w-6 items-center justify-center rounded-md px-1 text-xs ${style.className}`}
                      >
                        {STATE_SHORT[state]}
                        {cell && cell.products.length > 1 && (
                          <span className="ml-0.5 text-[10px] opacity-70">{cell.products.length}</span>
                        )}
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="px-3 py-2 text-[11px] text-zinc-600">
        ✓ действует · ! истекает в ближайшие 90 дней · ✕ истекла или проверено, что записи нет ·
        — не проверялось (запись не заведена)
      </div>
    </div>
  );
}
