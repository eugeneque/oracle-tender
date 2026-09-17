import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, MonitorCheck } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { PlatformSupportStatus, TenderUpperSoftware } from "../../api/types";
import { supportStatusStyle } from "../../utils/format";
import { CriticalityBadge } from "./TenderRequirementsTab";

// Интеграция в ПО верхнего уровня по закупке (замечание тестировщика 16.09.2026).
//
// Отдельный блок над матрицей соответствия: требование об интеграции — одно из самых
// частых, и ответ на него не «где-то в пятидесяти строках матрицы», а сразу: какое ПО
// названо в ТЗ и кто из производителей в его списке есть. Названные площадки — первыми
// и подсвечены; остальные показываются, когда требование об интеграции общее («в ПО
// верхнего уровня»), и свёрнуты, когда ПО названо конкретно.

const STATUS_SHORT: Record<PlatformSupportStatus, string> = {
  supported: "✓",
  not_listed: "✕",
  protocol_only: "~",
  not_synced: "—",
};

export function TenderUpperSoftwareBlock({ tenderId }: { tenderId: string }) {
  const [data, setData] = useState<TenderUpperSoftware | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    setData(null);
    setError(null);
    api
      .get<TenderUpperSoftware>(`/tenders/${tenderId}/upper-software`)
      .then(setData)
      .catch((err) =>
        setError(err instanceof ApiError ? err.message : "Не удалось загрузить сведения об интеграции")
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

  const named = new Set(data.named_platforms);
  const visiblePlatforms = data.platforms.filter(
    (p) => p.mentioned || data.generic_requirement || showAll
  );
  const hidden = data.platforms.length - visiblePlatforms.length;

  return (
    <div className="mb-4 rounded-xl border border-white/[0.08] bg-white/[0.02]">
      <div className="border-b border-white/[0.06] px-3 py-2.5">
        <div className="flex items-center gap-1.5 text-sm font-medium text-zinc-200">
          <MonitorCheck size={14} className="text-indigo-400" />
          Интеграция в ПО верхнего уровня
        </div>
        <ul className="mt-1.5 space-y-1">
          {data.requirements.map((requirement) => (
            <li key={requirement.id} className="text-[12px] leading-snug text-zinc-400">
              <span className="text-zinc-300">{requirement.text}</span>
              {requirement.criticality && <CriticalityBadge value={requirement.criticality} />}
              {requirement.platforms.length > 0 ? (
                <span className="text-indigo-400">
                  {" "}
                  →{" "}
                  {requirement.platforms
                    .map((key) => data.platforms.find((p) => p.adapter_key === key)?.name ?? key)
                    .join(", ")}
                </span>
              ) : (
                <span className="text-zinc-600"> → ПО не названо, проверяются все списки</span>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[480px] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-white/[0.06]">
              <th className="px-3 py-2 text-left text-xs font-medium text-zinc-500">Производитель</th>
              {visiblePlatforms.map((platform) => (
                <th
                  key={platform.adapter_key}
                  className={`px-1.5 py-2 text-center text-[11px] font-medium ${
                    named.has(platform.adapter_key) ? "text-indigo-300" : "text-zinc-500"
                  }`}
                  title={
                    platform.last_synced_at
                      ? `${platform.vendor}: ${platform.devices_total} записей`
                      : `${platform.vendor}: список ещё не прочитан`
                  }
                >
                  <span className="block max-w-[96px] truncate">{platform.name}</span>
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
                {visiblePlatforms.map((platform) => {
                  const cell = row.support[platform.adapter_key];
                  const status = cell?.status ?? "not_synced";
                  const style = supportStatusStyle(status);
                  const title =
                    status === "supported"
                      ? cell.devices
                          .map((d) => d.device_raw + (d.si_codes.length ? ` (ГРСИ ${d.si_codes.join(", ")})` : ""))
                          .join("; ") + (cell.devices_total > cell.devices.length ? ` и ещё ${cell.devices_total - cell.devices.length}` : "")
                      : cell?.note ?? style.label;
                  return (
                    <td key={platform.adapter_key} className="px-1.5 py-1.5 text-center">
                      <span
                        title={`${style.label}: ${title}`}
                        className={`inline-flex h-6 min-w-6 items-center justify-center rounded-md px-1 text-xs ${style.className}`}
                      >
                        {STATUS_SHORT[status]}
                        {status === "supported" && cell.devices_total > 1 && (
                          <span className="ml-0.5 text-[10px] opacity-70">{cell.devices_total}</span>
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

      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-[11px] text-zinc-600">
        <span>
          ✓ есть в списке · ✕ в списке нет · ~ только по протоколу СПОДЭС · — список не прочитан
        </span>
        {hidden > 0 && !showAll && (
          <button onClick={() => setShowAll(true)} className="flex items-center gap-1 text-zinc-400 hover:text-zinc-200">
            <ChevronRight size={12} /> ещё {hidden} ПО
          </button>
        )}
        {showAll && !data.generic_requirement && (
          <button onClick={() => setShowAll(false)} className="flex items-center gap-1 text-zinc-400 hover:text-zinc-200">
            <ChevronDown size={12} /> только названные
          </button>
        )}
      </div>
    </div>
  );
}
