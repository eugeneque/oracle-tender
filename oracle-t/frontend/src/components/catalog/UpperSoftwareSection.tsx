import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink, Loader2, MonitorCheck, RefreshCw } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { CatalogTask, PlatformSupport, UpperSoftwarePlatform } from "../../api/types";
import { formatDate, supportStatusStyle } from "../../utils/format";

// ПО верхнего уровня (замечание тестировщика 16.09.2026).
//
// Требование «интеграция в ПО верхнего уровня» стоит в ТЗ регулярно, и ответить на него по
// каталогу производителя нельзя: интегрирован прибор или нет, знает разработчик ПО и
// публикует списком поддерживаемого оборудования. Блок показывает семь таких списков и
// статус выбранного производителя на каждом: есть в списке (с моделями каталога, которые
// запись покрывает), нет в списке, только по протоколу СПОДЭС, список ещё не читали.
// Отсутствие в списке показывается так же явно, как присутствие — это и есть ответ на
// требование, а не пробел в данных.

function deviceDetails(device: PlatformSupport["devices"][number]): string | null {
  const details = device.details as {
    support?: Record<string, boolean>;
    functions?: string[];
    channels?: string[];
    via_protocol?: string;
    note?: string;
  };
  const parts: string[] = [];
  if (device.si_codes.length) parts.push(`ГРСИ ${device.si_codes.join(", ")}`);
  const flags = Object.entries(details.support ?? {})
    .filter(([, ok]) => ok)
    .map(([name]) => name);
  if (flags.length) parts.push(flags.join("; "));
  if (details.functions?.length) parts.push(details.functions.join(", "));
  if (details.channels?.length) parts.push(details.channels.join(", "));
  if (details.via_protocol) parts.push(`по протоколу ${details.via_protocol}`);
  if (details.note) parts.push(details.note);
  return parts.length ? parts.join(" · ") : null;
}

export function UpperSoftwareSection({
  manufacturerId,
  isAdmin,
}: {
  manufacturerId: string | null;
  isAdmin: boolean;
}) {
  const [open, setOpen] = useState(true);
  const [platforms, setPlatforms] = useState<UpperSoftwarePlatform[]>([]);
  const [support, setSupport] = useState<PlatformSupport[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (id: string | null) => {
    const [list, rows] = await Promise.all([
      api.get<UpperSoftwarePlatform[]>("/catalog/upper-software"),
      id ? api.get<PlatformSupport[]>(`/manufacturers/${id}/upper-software`) : Promise.resolve([]),
    ]);
    setPlatforms(list);
    setSupport(rows);
  }, []);

  useEffect(() => {
    setError(null);
    setNotice(null);
    setExpanded(null);
    load(manufacturerId).catch((err) =>
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить списки ПО верхнего уровня")
    );
  }, [manufacturerId, load]);

  const handleSync = async (adapterKey: string, name: string) => {
    setBusy(adapterKey);
    setError(null);
    try {
      await api.post<CatalogTask>(`/catalog/upper-software/${adapterKey}/sync`);
      setNotice(
        `Чтение списка «${name}» поставлено в очередь: это секунды, итог — в очереди справочника и журнале. ` +
          "Обновите блок через минуту."
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось поставить чтение списка в очередь");
    } finally {
      setBusy(null);
    }
  };

  const handleReload = async () => {
    setBusy("reload");
    try {
      await load(manufacturerId);
    } finally {
      setBusy(null);
    }
  };

  const byKey = new Map(support.map((row) => [row.key, row]));
  const supportedCount = support.filter((row) => row.status === "supported").length;
  const readCount = platforms.filter((p) => p.last_synced_at).length;

  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <div
        className={`flex flex-wrap items-center justify-between gap-2 px-5 py-3 ${
          open ? "border-b border-white/[0.08]" : ""
        }`}
      >
        <button onClick={() => setOpen((v) => !v)} className="flex items-start gap-2 text-left" aria-expanded={open}>
          <span className="mt-0.5 text-zinc-500">{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
          <span>
            <h2 className="text-sm font-semibold text-zinc-100">
              ПО верхнего уровня
              {manufacturerId && support.length > 0 && (
                <span className="ml-1.5 text-zinc-500">
                  · в списках {supportedCount} из {readCount}
                </span>
              )}
            </h2>
            <p className="mt-0.5 text-xs text-zinc-500">
              Списки поддерживаемого оборудования на сайтах разработчиков АСКУЭ. По ним проверяется
              требование «интеграция в ПО верхнего уровня»: отсутствие в списке — ответ, а не пробел.
            </p>
          </span>
        </button>
        <button
          onClick={handleReload}
          disabled={busy !== null}
          title="Перечитать состояние списков"
          className="flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-xs text-zinc-300 hover:bg-white/5 disabled:opacity-50"
        >
          {busy === "reload" ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
          Обновить
        </button>
      </div>

      {(notice || error) && (
        <div className="px-5 pt-3">
          {error && (
            <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400">{error}</div>
          )}
          {notice && (
            <div className="flex items-start justify-between gap-3 rounded-lg border border-indigo-500/20 bg-indigo-500/10 px-3 py-2 text-xs text-indigo-300">
              <span>{notice}</span>
              <button onClick={() => setNotice(null)} className="shrink-0 text-indigo-400 hover:text-indigo-200">
                ×
              </button>
            </div>
          )}
        </div>
      )}

      <div className="px-5 py-3" hidden={!open}>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="text-[11px] uppercase tracking-wide text-zinc-500">
                <th className="py-1.5 pr-4 font-medium">ПО</th>
                <th className="py-1.5 pr-4 font-medium">Список</th>
                <th className="py-1.5 pr-4 font-medium">Производитель</th>
                {isAdmin && <th className="py-1.5 font-medium" />}
              </tr>
            </thead>
            <tbody>
              {platforms.map((platform) => {
                const row = byKey.get(platform.adapter_key);
                const status = row ? supportStatusStyle(row.status) : null;
                const isExpanded = expanded === platform.adapter_key;
                const canExpand = row !== undefined && row.devices.length > 0;
                return (
                  <FragmentRow
                    key={platform.adapter_key}
                    platform={platform}
                    row={row}
                    status={status}
                    isAdmin={isAdmin}
                    busy={busy}
                    isExpanded={isExpanded}
                    canExpand={canExpand}
                    onToggle={() => setExpanded(isExpanded ? null : platform.adapter_key)}
                    onSync={() => handleSync(platform.adapter_key, platform.name)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function FragmentRow({
  platform,
  row,
  status,
  isAdmin,
  busy,
  isExpanded,
  canExpand,
  onToggle,
  onSync,
}: {
  platform: UpperSoftwarePlatform;
  row: PlatformSupport | undefined;
  status: { label: string; className: string } | null;
  isAdmin: boolean;
  busy: string | null;
  isExpanded: boolean;
  canExpand: boolean;
  onToggle: () => void;
  onSync: () => void;
}) {
  const synced = formatDate(platform.last_synced_at);
  return (
    <>
      <tr
        className={`border-t border-white/[0.06] ${canExpand ? "cursor-pointer hover:bg-white/[0.03]" : ""}`}
        onClick={canExpand ? onToggle : undefined}
      >
        <td className="py-2 pr-4 align-top">
          <div className="flex items-center gap-1.5">
            {canExpand && (
              <ChevronRight
                size={13}
                className={`shrink-0 text-zinc-600 transition-transform ${isExpanded ? "rotate-90" : ""}`}
              />
            )}
            <span className="text-zinc-200">{platform.name}</span>
          </div>
          <div className="text-[11px] text-zinc-500">
            {platform.vendor}
            {" · "}
            <a
              href={platform.url}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="inline-flex items-center gap-0.5 text-indigo-400 hover:text-indigo-300"
            >
              список <ExternalLink size={10} />
            </a>
          </div>
        </td>
        <td className="py-2 pr-4 align-top text-[12px] text-zinc-400">
          {platform.last_synced_at ? (
            <>
              {platform.devices_total} записей, из них по нашим производителям {platform.devices_matched}
              <div className="text-[11px] text-zinc-600">прочитан {synced}</div>
            </>
          ) : (
            <span className="text-zinc-500">ещё не читали</span>
          )}
          {platform.availability_status === "unavailable" && (
            <div className="text-[11px] text-red-400">сайт недоступен</div>
          )}
        </td>
        <td className="py-2 pr-4 align-top">
          {status && row ? (
            <>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${status.className}`}
              >
                <MonitorCheck size={11} />
                {status.label}
                {row.status === "supported" && <span className="opacity-70">· {row.devices.length}</span>}
              </span>
              {row.note && row.status !== "supported" && (
                <div className="mt-1 max-w-md text-[11px] text-zinc-500">{row.note}</div>
              )}
            </>
          ) : (
            <span className="text-[12px] text-zinc-600">—</span>
          )}
        </td>
        {isAdmin && (
          <td className="py-2 text-right align-top">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onSync();
              }}
              disabled={busy !== null}
              title="Прочитать список с сайта разработчика ПО (в фоне)"
              className="inline-flex items-center gap-1 rounded-lg border border-white/10 px-2 py-1 text-[11px] text-zinc-300 hover:bg-white/5 disabled:opacity-50"
            >
              {busy === platform.adapter_key ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
              Прочитать
            </button>
          </td>
        )}
      </tr>
      {isExpanded && row && (
        <tr className="border-t border-white/[0.04]">
          <td colSpan={isAdmin ? 4 : 3} className="bg-white/[0.02] px-3 py-2.5">
            <ul className="space-y-1.5">
              {row.devices.map((device) => {
                const extra = deviceDetails(device);
                return (
                  <li key={device.id} className="text-[12px] leading-snug">
                    <span className="text-zinc-200">{device.device_raw}</span>
                    {device.section && <span className="text-zinc-600"> — {device.section}</span>}
                    {extra && <div className="text-[11px] text-zinc-500">{extra}</div>}
                    {device.products.length > 0 && (
                      <div className="text-[11px] text-emerald-400/80">
                        покрывает модели каталога: {device.products.join(", ")}
                      </div>
                    )}
                    {device.manufacturer_matched_by === "device_brand" && (
                      <div className="text-[11px] text-zinc-600">
                        производитель на сайте не назван — определён по обозначению прибора
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </td>
        </tr>
      )}
    </>
  );
}
