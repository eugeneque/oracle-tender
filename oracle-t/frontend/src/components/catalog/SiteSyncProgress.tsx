import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "../../api/client";
import type { CatalogTask } from "../../api/types";

// Ход обхода сайта производителя: полоса «N из M», сколько прошло и сколько примерно осталось
// (14.10.2026). Раньше после кнопки «Модели с сайта» было только «займёт несколько минут» —
// непонятно, идёт ли обход и когда он кончится. Ход пишет сам обход в `details.progress`
// задачи очереди; здесь он опрашивается, пока задача не завершится.

const POLL_MS = 2000;
const ACTIVE = new Set(["queued", "running"]);

function seconds(from: string | null | undefined, to: number): number | null {
  if (!from) return null;
  const start = Date.parse(from);
  return Number.isNaN(start) ? null : Math.max(0, (to - start) / 1000);
}

function clock(total: number): string {
  const s = Math.round(total);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function remainingLabel(sec: number): string {
  if (sec < 45) return "меньше минуты";
  const min = Math.round(sec / 60);
  return `~${min} мин`;
}

export function SiteSyncProgress({
  adapterKey,
  kick,
  onActiveChange,
  onFinished,
}: {
  adapterKey: string;
  /** Меняется после нажатия кнопки — повод сразу заново опросить очередь. */
  kick: number;
  onActiveChange: (active: boolean) => void;
  onFinished: (task: CatalogTask) => void;
}) {
  const [task, setTask] = useState<CatalogTask | null>(null);
  // Длительность прошлого обхода — оценка, пока не известно число карточек.
  const [lastDuration, setLastDuration] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const activeId = useRef<string | null>(null);
  const callbacks = useRef({ onActiveChange, onFinished });
  callbacks.current = { onActiveChange, onFinished };

  useEffect(() => {
    let cancelled = false;
    let timer: number | null = null;
    activeId.current = null;
    setTask(null);

    const poll = async () => {
      try {
        const tasks = await api.get<CatalogTask[]>(
          `/catalog/queue?adapter_key=${encodeURIComponent(adapterKey)}&limit=10`,
        );
        if (cancelled) return;
        const active = tasks.find((t) => ACTIVE.has(t.status)) ?? null;
        const done = tasks.find((t) => t.status !== "error" && t.started_at && t.finished_at);
        if (done) setLastDuration((Date.parse(done.finished_at!) - Date.parse(done.started_at!)) / 1000);

        if (!active && activeId.current) {
          const finished = tasks.find((t) => t.id === activeId.current);
          if (finished) callbacks.current.onFinished(finished);
        }
        activeId.current = active?.id ?? null;
        setTask(active);
        callbacks.current.onActiveChange(active !== null);
        if (active) timer = window.setTimeout(poll, POLL_MS);
      } catch {
        // Сбой опроса — попробуем ещё раз чуть позже, полоса просто не обновится.
        if (!cancelled) timer = window.setTimeout(poll, POLL_MS * 3);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [adapterKey, kick]);

  useEffect(() => {
    if (!task) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [task]);

  if (!task) return null;

  const progress = task.details?.progress;
  const total = progress?.total ?? 0;
  const done = progress?.done ?? 0;
  const elapsed = seconds(task.started_at, now);
  const running = task.status === "running";

  let remaining: number | null = null;
  if (running && elapsed !== null) {
    if (total > 0 && done > 0) remaining = (elapsed / done) * (total - done);
    else if (lastDuration) remaining = Math.max(0, lastDuration - elapsed);
  }
  const share = total > 0 ? done / total : null;

  let caption: string;
  if (!running) caption = "В очереди — обход начнётся, как только освободится исполнитель";
  else if (!progress || total === 0) caption = "Читаем страницы категорий на сайте";
  else if (done >= total) caption = progress.current ?? "Завершаем";
  else caption = `Модель ${done + 1} из ${total}${progress.current ? ` · ${progress.current}` : ""}`;

  return (
    <div className="mt-3 max-w-xl" aria-live="polite">
      <div className="flex items-center justify-between gap-4 text-xs">
        <span className="flex min-w-0 items-center gap-1.5 text-zinc-300">
          <Loader2 size={12} className="shrink-0 animate-spin text-indigo-400" />
          <span className="truncate">{caption}</span>
        </span>
        <span className="shrink-0 tabular-nums text-zinc-500">
          {elapsed !== null && running && <>прошло {clock(elapsed)}</>}
          {remaining !== null && <> · осталось {remainingLabel(remaining)}</>}
        </span>
      </div>
      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-white/[0.06]">
        {share !== null ? (
          <div
            className="h-full rounded-full bg-indigo-400 transition-[width] duration-700"
            style={{ width: `${Math.max(2, share * 100)}%` }}
          />
        ) : (
          // Сколько всего карточек, ещё не известно — полоса «бегает», а не стоит на нуле.
          <div className="h-full w-1/3 animate-pulse rounded-full bg-indigo-400/60" />
        )}
      </div>
    </div>
  );
}
