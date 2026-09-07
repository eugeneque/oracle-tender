import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, Pause, Play, ScrollText } from "lucide-react";

import { ApiError, api } from "../../api/client";
import type { LogEntry, LogFacets, LogPage } from "../../api/types";

const REFRESH_MS = 5_000;
const PAGE_SIZE = 200;

const LEVEL_STYLES: Record<LogEntry["level"], string> = {
  INFO: "text-sky-400",
  WARNING: "text-amber-400",
  ERROR: "text-red-400",
  CRITICAL: "text-red-300",
};

const PERIODS: { label: string; hours: number | null }[] = [
  { label: "1 час", hours: 1 },
  { label: "24 часа", hours: 24 },
  { label: "7 дней", hours: 24 * 7 },
  { label: "всё время", hours: null },
];

function formatTime(value: string): string {
  const date = new Date(value);
  // Формат терминала: дата — только в подсказке, в строке важны часы-минуты-секунды.
  return date.toLocaleTimeString("ru-RU", { hour12: false });
}

/**
 * Раздел «Логирование» (раздел 5.9 ТЗ) — журнал операций в виде, близком к терминалу.
 *
 * Свёрнут по умолчанию: это диагностический инструмент, а не то, ради чего открывают
 * настройки, и постоянный поток строк на экране мешал бы остальным разделам. Пока раздел
 * свёрнут, автообновление не идёт — незачем опрашивать сервер каждые пять секунд ради
 * блока, который никто не видит.
 *
 * Доступен всем пользователям: по журналу человек понимает, почему у тендера нет требований
 * или почему площадка не опрашивалась.
 */
export function LogsSection() {
  const [isExpanded, setIsExpanded] = useState(false);
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [facets, setFacets] = useState<LogFacets | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const [levels, setLevels] = useState<string[]>([]);
  const [component, setComponent] = useState("");
  const [search, setSearch] = useState("");
  const [hours, setHours] = useState<number | null>(24);
  const [isLive, setIsLive] = useState(true);

  const consoleRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const params = new URLSearchParams();
    levels.forEach((level) => params.append("level", level));
    if (component) params.set("component", component);
    if (search.trim()) params.set("search", search.trim());
    if (hours !== null) params.set("hours", String(hours));
    params.set("limit", String(PAGE_SIZE));

    try {
      const page = await api.get<LogPage>(`/logs?${params.toString()}`);
      // С сервера строки приходят от новых к старым — в терминале удобнее наоборот,
      // сверху вниз по времени, как в `tail -f`.
      setEntries([...page.items].reverse());
      setTotal(page.total);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Не удалось загрузить журнал");
    } finally {
      setIsLoading(false);
    }
  }, [levels, component, search, hours]);

  useEffect(() => {
    if (!isExpanded) return;
    setIsLoading(true);
    void load();
  }, [isExpanded, load]);

  // Список компонентов подгружается один раз при первом раскрытии: он меняется только с
  // выходом новых версий системы, тянуть его вместе с каждым обновлением журнала незачем.
  useEffect(() => {
    if (!isExpanded || facets) return;
    api
      .get<LogFacets>("/logs/facets")
      .then(setFacets)
      .catch(() => setFacets({ components: [], levels: [] }));
  }, [isExpanded, facets]);

  useEffect(() => {
    if (!isExpanded || !isLive) return;
    const interval = setInterval(() => void load(), REFRESH_MS);
    return () => clearInterval(interval);
  }, [isExpanded, isLive, load]);

  // Автопрокрутка к последней строке — но только в режиме слежения: если человек
  // отлистал вверх и поставил паузу, дёргать его скролл нельзя.
  useEffect(() => {
    if (!isLive || !consoleRef.current) return;
    consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
  }, [entries, isLive]);

  const toggleLevel = (level: string) => {
    setLevels((current) =>
      current.includes(level) ? current.filter((item) => item !== level) : [...current, level],
    );
  };

  return (
    <div className="mt-6 overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.03]">
      <button
        onClick={() => setIsExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-5 py-4 text-left"
      >
        <div>
          <h2 className="flex items-center gap-2 text-sm font-semibold text-zinc-100">
            <ScrollText size={15} className="text-indigo-400" />
            Логирование
          </h2>
          <p className="mt-0.5 text-xs text-zinc-500">
            Журнал операций системы (раздел 5.9 ТЗ): опрос источников, разбор документов,
            ИИ-анализ, действия пользователей. Обновляется автоматически, пока раздел открыт.
          </p>
        </div>
        <ChevronDown
          size={18}
          className={`shrink-0 text-zinc-500 transition-transform ${isExpanded ? "rotate-180" : ""}`}
        />
      </button>

      {isExpanded && (
        <div className="border-t border-white/[0.08] px-5 py-4">
          {error && (
            <div className="mb-3 rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-400">
              {error}
            </div>
          )}

          <div className="mb-3 flex flex-wrap items-center gap-2">
            {(["INFO", "WARNING", "ERROR", "CRITICAL"] as const).map((level) => (
              <button
                key={level}
                onClick={() => toggleLevel(level)}
                className={`rounded-lg border px-2.5 py-1.5 text-xs transition-colors ${
                  levels.includes(level)
                    ? "border-indigo-500/40 bg-indigo-500/15 text-indigo-300"
                    : "border-white/10 text-zinc-400 hover:bg-white/5"
                }`}
              >
                {level}
              </button>
            ))}

            <select
              value={component}
              onChange={(e) => setComponent(e.target.value)}
              className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs text-zinc-300 focus:border-indigo-500/50 focus:outline-none"
            >
              <option value="">все компоненты</option>
              {facets?.components.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>

            <select
              value={hours === null ? "" : String(hours)}
              onChange={(e) => setHours(e.target.value === "" ? null : Number(e.target.value))}
              className="rounded-lg border border-white/10 bg-black/20 px-2.5 py-1.5 text-xs text-zinc-300 focus:border-indigo-500/50 focus:outline-none"
            >
              {PERIODS.map((period) => (
                <option key={period.label} value={period.hours === null ? "" : String(period.hours)}>
                  {period.label}
                </option>
              ))}
            </select>

            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="поиск по действию и деталям…"
              className="min-w-[200px] flex-1 rounded-lg border border-white/10 bg-black/20 px-3 py-1.5 text-xs text-zinc-200 placeholder:text-zinc-600 focus:border-indigo-500/50 focus:outline-none"
            />

            <button
              onClick={() => setIsLive((v) => !v)}
              title={isLive ? "Остановить автообновление" : "Возобновить автообновление"}
              className={`flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs ${
                isLive
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                  : "border-white/10 text-zinc-400 hover:bg-white/5"
              }`}
            >
              {isLive ? <Pause size={12} /> : <Play size={12} />}
              {isLive ? "слежение" : "пауза"}
            </button>
          </div>

          <div
            ref={consoleRef}
            className="h-80 overflow-y-auto rounded-lg border border-white/[0.06] bg-black/40 p-3 font-mono text-xs leading-relaxed"
          >
            {isLoading && entries.length === 0 ? (
              <div className="text-zinc-600">Загружаю журнал…</div>
            ) : entries.length === 0 ? (
              <div className="text-zinc-600">Записей по этим условиям нет.</div>
            ) : (
              entries.map((entry) => (
                <div key={entry.id} className="whitespace-pre-wrap break-words text-zinc-400">
                  <span className="text-zinc-600" title={new Date(entry.timestamp).toLocaleString("ru-RU")}>
                    {formatTime(entry.timestamp)}
                  </span>{" "}
                  <span className={LEVEL_STYLES[entry.level]}>{entry.level.padEnd(8)}</span>
                  <span className="text-zinc-500">| {entry.component} | </span>
                  <span className="text-zinc-300">{entry.action}</span>
                  <span className="text-zinc-500"> → {entry.result}</span>
                  {entry.details && <span className="text-zinc-500"> — {entry.details}</span>}
                  {entry.user_name && <span className="text-indigo-400/70"> [{entry.user_name}]</span>}
                </div>
              ))
            )}
          </div>

          <p className="mt-2 text-xs text-zinc-500">
            Показано {entries.length} из {total} записей по текущему фильтру
            {entries.length < total ? " (сначала самые свежие)" : ""} · хранение журнала — 6 месяцев
          </p>
        </div>
      )}
    </div>
  );
}

