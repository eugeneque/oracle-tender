import { Layers, Loader2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ApiError, api } from "../../api/client";
import type { JobQueueItem, UserJobQueue } from "../../api/types";
import { queueText } from "../../utils/jobQueue";

// Пока что-то идёт — чаще: человек ждёт, когда его закупка начнётся. Пусто — редко.
const BUSY_POLL_MS = 5000;
const IDLE_POLL_MS = 30000;

/**
 * «Мои разборы» в шапке (29.09.2026): у каждого пользователя разбирается одна закупка за
 * раз, остальные ждут своей очереди — и видно, что именно ждёт и почему. Пока очередь
 * пуста, значка нет.
 */
export function MyJobsIndicator() {
  const [queue, setQueue] = useState<UserJobQueue | null>(null);
  const [isOpen, setIsOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const reload = async () => {
    try {
      setQueue((await api.get<UserJobQueue[]>("/jobs/queue?scope=mine"))[0] ?? null);
    } catch {
      // Следующий опрос всё равно обновит список.
    }
  };

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      let next: UserJobQueue | null = null;
      try {
        next = (await api.get<UserJobQueue[]>("/jobs/queue?scope=mine"))[0] ?? null;
      } catch {
        // Нет связи — оставляем прежнее состояние и пробуем позже.
        next = queue;
      }
      if (cancelled) return;
      setQueue(next);
      const busy = next !== null && next.running.length + next.queued.length > 0;
      timer = setTimeout(() => void poll(), busy ? BUSY_POLL_MS : IDLE_POLL_MS);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // Опрос запускается один раз на шапку; `queue` в замыкании нужен только как запасное значение.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    const onDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setIsOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [isOpen]);

  const running = queue?.running.length ?? 0;
  const waiting = queue?.queued.length ?? 0;
  if (running + waiting === 0) return null;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((value) => !value)}
        aria-expanded={isOpen}
        title="Мои разборы закупок"
        className="flex h-8 items-center gap-1.5 rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-2.5 text-xs text-indigo-200 hover:bg-indigo-500/15"
      >
        {running > 0 ? <Loader2 size={13} className="animate-spin" /> : <Layers size={13} />}
        {running > 0 ? `Разбор идёт` : "Разборы ждут"}
        {waiting > 0 && (
          <span className="rounded-full bg-indigo-500/25 px-1.5 text-[11px] leading-4">+{waiting} в очереди</span>
        )}
      </button>
      {isOpen && queue && (
        <div className="absolute right-0 top-10 z-50 w-96 rounded-xl border border-white/10 bg-zinc-900 p-2 shadow-xl shadow-black/40">
          <p className="px-2 pb-1.5 pt-1 text-[11px] text-zinc-500">
            Одновременно разбирается одна ваша закупка, остальные ждут по порядку запуска.
          </p>
          {queue.running.map((item) => (
            <QueueRow key={item.id} item={item} onOpen={() => setIsOpen(false)} onCancelled={reload} />
          ))}
          {queue.queued.length > 0 && (
            <p className="px-2 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wide text-zinc-500">
              В очереди
            </p>
          )}
          {queue.queued.map((item, index) => (
            <QueueRow
              key={item.id}
              item={item}
              position={index + 1}
              onOpen={() => setIsOpen(false)}
              onCancelled={reload}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function QueueRow({
  item,
  position,
  onOpen,
  onCancelled,
}: {
  item: JobQueueItem;
  position?: number;
  onOpen?: () => void;
  /** Задан — у строки есть кнопка «убрать из очереди» (29.09.2026). */
  onCancelled?: () => void;
}) {
  const [isCancelling, setIsCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const detail =
    item.status === "queued" ? queueText(item.queue_reason, item.queue_ahead) : item.message;
  const body = (
    <>
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white/5 text-[10px] text-zinc-400">
        {item.status === "running" ? <Loader2 size={11} className="animate-spin text-indigo-300" /> : position ?? "•"}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs text-zinc-200">
          {item.tender_title ?? item.kind_label}
        </span>
        <span className="block text-[11px] text-zinc-500">
          {item.kind_label}
          {item.tender_external_id ? ` · № ${item.tender_external_id}` : ""}
        </span>
        {detail && <span className="mt-0.5 block text-[11px] text-indigo-300/80">{detail}</span>}
        {cancelError && <span className="mt-0.5 block text-[11px] text-red-300">{cancelError}</span>}
      </span>
    </>
  );

  const cancel = async () => {
    setIsCancelling(true);
    setCancelError(null);
    try {
      await api.post(`/jobs/${item.id}/cancel`);
    } catch (err) {
      // 409 — задача успела закончиться сама: строка просто исчезнет при обновлении.
      if (!(err instanceof ApiError && err.status === 409)) {
        setCancelError(err instanceof ApiError ? err.message : "Не удалось отменить");
        setIsCancelling(false);
        return;
      }
    }
    setIsCancelling(false);
    onCancelled?.();
  };

  const className = "flex min-w-0 flex-1 items-start gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-white/5";
  return (
    <div className="group flex items-start gap-1">
      {item.tender_id ? (
        <Link to={`/tenders/${item.tender_id}`} onClick={onOpen} className={className}>
          {body}
        </Link>
      ) : (
        <div className={className}>{body}</div>
      )}
      {onCancelled && (
        <button
          type="button"
          onClick={() => void cancel()}
          disabled={isCancelling}
          aria-label={item.status === "running" ? "Остановить разбор" : "Убрать из очереди"}
          title={
            item.status === "running"
              ? "Остановить разбор — он прервётся на ближайшем запросе к модели, сохранённое останется"
              : "Убрать из очереди"
          }
          className="mt-1.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-zinc-500 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50"
        >
          {isCancelling ? <Loader2 size={12} className="animate-spin" /> : <X size={13} />}
        </button>
      )}
    </div>
  );
}
