import { AlertCircle, Bell, CheckCircle2, Clock, Loader2, Volume2, VolumeX, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";

import { ACTIVITY_EVENT, ApiError, api } from "../../api/client";
import type { ActivityFeed, ActivityItem } from "../../api/types";

// Пока что-то идёт — часто: человек ждёт итога и шкалы. Пусто — редко.
const BUSY_POLL_MS = 4000;
const IDLE_POLL_MS = 20000;
const TOAST_MS = 7000;

const STORAGE_SEEN = "oraclet.activity.seenAt";
const STORAGE_NOTIFIED = "oraclet.activity.notified";
const STORAGE_MUTED = "oraclet.activity.muted";

const SOUNDS = { success: "/sounds/success.mp3", error: "/sounds/error.mp3" } as const;

const isActive = (item: ActivityItem) => item.status === "queued" || item.status === "running";

function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStorage(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Приватное окно — сигналы просто не запоминаются между перезагрузками.
  }
}

/** Отметить, что об окончании уже сообщили. Общая отметка на все вкладки: открыто три
 *  вкладки — сигнал звучит один раз, а не трижды. Возвращает false, если уже отмечено. */
function markNotified(id: string): boolean {
  let list: string[] = [];
  try {
    list = JSON.parse(readStorage(STORAGE_NOTIFIED) ?? "[]");
  } catch {
    list = [];
  }
  if (list.includes(id)) return false;
  writeStorage(STORAGE_NOTIFIED, JSON.stringify([id, ...list].slice(0, 200)));
  return true;
}

function playSound(kind: keyof typeof SOUNDS) {
  if (readStorage(STORAGE_MUTED) === "1") return;
  const audio = new Audio(SOUNDS[kind]);
  audio.volume = 0.6;
  // Браузер не даёт играть звук без действия пользователя на странице — молча пропускаем.
  void audio.play().catch(() => undefined);
}

function timeText(iso: string): string {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  return new Date(iso).toLocaleDateString("ru-RU");
}

function resultText(item: ActivityItem): string {
  if (item.status === "error") return "Ошибка";
  if (item.status === "cancelled") return "Остановлено";
  return "Готово";
}

/**
 * Центр уведомлений в шапке (14.10.2026): запуски пользователя — разбор закупки, опрос
 * площадок, опрос каталога, обход сайта производителя — и итоги по ним за сутки, плюс
 * общие процессы (плановый опрос площадок, очередь справочника) со шкалой хода. Когда
 * свой процесс заканчивается — звуковой сигнал (успех или ошибка) и всплывающая плашка.
 * Заменил «Мои разборы»: те показывали только очередь разборов, без итогов.
 */
export function NotificationCenter() {
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [toasts, setToasts] = useState<ActivityItem[]>([]);
  const [muted, setMuted] = useState(() => readStorage(STORAGE_MUTED) === "1");
  const [seenAt, setSeenAt] = useState(() => Number(readStorage(STORAGE_SEEN) ?? 0));
  const rootRef = useRef<HTMLDivElement>(null);
  // Статусы с прошлого опроса: сигнал — только на переход «шло → закончилось» или на
  // итог, появившийся после открытия страницы (процесс закончился, пока вкладка спала).
  const known = useRef<Map<string, ActivityItem["status"]>>(new Map());
  const mountedAt = useRef(Date.now());
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const loadRef = useRef<() => Promise<void>>();

  const notify = useCallback((item: ActivityItem) => {
    if (!markNotified(item.id)) return;
    playSound(item.status === "success" ? "success" : "error");
    setToasts((list) => [item, ...list.filter((t) => t.id !== item.id)].slice(0, 3));
    setTimeout(() => setToasts((list) => list.filter((t) => t.id !== item.id)), TOAST_MS);
  }, []);

  const load = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    let next: ActivityItem[] | null = null;
    try {
      next = (await api.get<ActivityFeed>("/activity")).items;
    } catch {
      // Нет связи — оставляем прежний список и пробуем позже.
    }
    if (next) {
      for (const item of next) {
        if (item.scope !== "mine" || isActive(item) || item.status === "cancelled") continue;
        const before = known.current.get(item.id);
        const finishedLater =
          before === undefined &&
          item.finished_at !== null &&
          new Date(item.finished_at).getTime() > mountedAt.current;
        if ((before !== undefined && before !== item.status) || finishedLater) notify(item);
      }
      known.current = new Map(next.map((item) => [item.id, item.status]));
      setItems(next);
    }
    const busy = (next ?? []).some(isActive);
    timer.current = setTimeout(() => void loadRef.current?.(), busy ? BUSY_POLL_MS : IDLE_POLL_MS);
  }, [notify]);
  loadRef.current = load;

  useEffect(() => {
    void load();
    // Пользователь что-то запустил — перечитать через секунду, когда задача уже в очереди.
    let soon: ReturnType<typeof setTimeout> | undefined;
    const onLaunch = () => {
      if (soon) clearTimeout(soon);
      soon = setTimeout(() => void loadRef.current?.(), 1000);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") void loadRef.current?.();
    };
    window.addEventListener(ACTIVITY_EVENT, onLaunch);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      if (timer.current) clearTimeout(timer.current);
      if (soon) clearTimeout(soon);
      window.removeEventListener(ACTIVITY_EVENT, onLaunch);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

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

  const open = () => {
    setIsOpen((value) => !value);
    const now = Date.now();
    setSeenAt(now);
    writeStorage(STORAGE_SEEN, String(now));
  };

  const toggleMute = () => {
    const next = !muted;
    setMuted(next);
    writeStorage(STORAGE_MUTED, next ? "1" : "0");
  };

  const active = items.filter(isActive);
  const finished = items.filter((item) => !isActive(item));
  const unread = finished.filter(
    (item) => item.scope === "mine" && item.finished_at && new Date(item.finished_at).getTime() > seenAt,
  ).length;
  const hasError = finished.some(
    (item) =>
      item.scope === "mine" &&
      item.status === "error" &&
      item.finished_at &&
      new Date(item.finished_at).getTime() > seenAt,
  );

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={open}
        aria-expanded={isOpen}
        aria-label="Уведомления и процессы"
        title={active.length ? `Идёт процессов: ${active.length}` : "Уведомления"}
        className={`relative flex h-8 w-8 items-center justify-center rounded-full transition-colors ${
          isOpen ? "bg-white/10 text-zinc-100" : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
        }`}
      >
        {active.length > 0 ? <Loader2 size={16} className="animate-spin text-indigo-300" /> : <Bell size={16} />}
        {unread > 0 && (
          <span
            className={`absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-medium leading-none text-white ${
              hasError ? "bg-red-500" : "bg-indigo-500"
            }`}
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="absolute right-0 top-10 z-50 w-[22rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-xl shadow-black/40">
          <div className="flex items-center justify-between border-b border-white/[0.06] px-3 py-2.5">
            <span className="text-sm font-medium text-zinc-100">Уведомления</span>
            <button
              type="button"
              onClick={toggleMute}
              title={muted ? "Включить звук" : "Выключить звук"}
              aria-label={muted ? "Включить звук" : "Выключить звук"}
              className="flex h-7 w-7 items-center justify-center rounded-md text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
            >
              {muted ? <VolumeX size={14} /> : <Volume2 size={14} />}
            </button>
          </div>
          <div className="max-h-[70vh] overflow-y-auto p-1.5">
            {items.length === 0 && (
              <p className="px-3 py-8 text-center text-xs text-zinc-500">
                Здесь появятся запущенные вами процессы и их итоги — разбор закупки, опрос площадок,
                опрос каталога.
              </p>
            )}
            {active.length > 0 && <SectionTitle>Сейчас идёт</SectionTitle>}
            {active.map((item) => (
              <ActivityRow key={item.id} item={item} onOpen={() => setIsOpen(false)} onChanged={load} />
            ))}
            {finished.length > 0 && <SectionTitle>За сутки</SectionTitle>}
            {finished.map((item) => (
              <ActivityRow
                key={item.id}
                item={item}
                isNew={item.scope === "mine" && !!item.finished_at && new Date(item.finished_at).getTime() > seenAt}
                onOpen={() => setIsOpen(false)}
              />
            ))}
          </div>
        </div>
      )}

      {/* В `body`, а не в шапке: `backdrop-blur` шапки делает её опорой для `fixed`, и
          плашка оказывалась под шапкой вместо правого нижнего угла. */}
      {toasts.length > 0 &&
        createPortal(
          <div className="fixed bottom-5 right-5 z-[60] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
            {toasts.map((item) => (
              <Toast key={item.id} item={item} onClose={() => setToasts((list) => list.filter((t) => t.id !== item.id))} />
            ))}
          </div>,
          document.body,
        )}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <p className="px-2.5 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wide text-zinc-500">{children}</p>;
}

function StatusIcon({ item }: { item: ActivityItem }) {
  if (item.status === "running") return <Loader2 size={14} className="animate-spin text-indigo-300" />;
  if (item.status === "queued") return <Clock size={14} className="text-zinc-500" />;
  if (item.status === "error") return <AlertCircle size={14} className="text-red-400" />;
  if (item.status === "cancelled") return <X size={14} className="text-zinc-500" />;
  return <CheckCircle2 size={14} className="text-emerald-400" />;
}

function ProgressBar({ value }: { value: number | null }) {
  return (
    <span className="mt-1.5 block h-1 overflow-hidden rounded-full bg-white/[0.06]">
      {value === null ? (
        <span className="block h-full w-1/3 animate-[activity-indeterminate_1.4s_ease-in-out_infinite] rounded-full bg-indigo-400/70" />
      ) : (
        <span
          className="block h-full rounded-full bg-indigo-400 transition-[width] duration-500"
          style={{ width: `${Math.max(3, Math.round(value * 100))}%` }}
        />
      )}
    </span>
  );
}

function ActivityRow({
  item,
  isNew = false,
  onOpen,
  onChanged,
}: {
  item: ActivityItem;
  isNew?: boolean;
  onOpen: () => void;
  onChanged?: () => void;
}) {
  const [isCancelling, setIsCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const running = isActive(item);
  const meta = [
    item.subtitle,
    item.owner_name ? `запустил(а) ${item.owner_name}` : null,
    running ? null : timeText(item.finished_at ?? item.started_at),
  ]
    .filter(Boolean)
    .join(" · ");
  const percent = running && item.progress !== null ? ` · ${Math.round(item.progress * 100)}%` : "";

  const body = (
    <>
      <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center">
        <StatusIcon item={item} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-xs text-zinc-100">{item.title}</span>
          {isNew && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-400" />}
        </span>
        {meta && <span className="block truncate text-[11px] text-zinc-500">{meta}{percent}</span>}
        {item.message && (
          <span
            className={`mt-0.5 line-clamp-2 block text-[11px] ${
              item.status === "error" ? "text-red-300/90" : running ? "text-indigo-300/80" : "text-zinc-400"
            }`}
          >
            {running ? item.message : `${resultText(item)}: ${item.message}`}
          </span>
        )}
        {running && <ProgressBar value={item.status === "queued" ? 0 : item.progress} />}
        {error && <span className="mt-0.5 block text-[11px] text-red-300">{error}</span>}
      </span>
    </>
  );

  const cancel = async () => {
    if (!item.job_id) return;
    setIsCancelling(true);
    setError(null);
    try {
      await api.post(`/jobs/${item.job_id}/cancel`);
    } catch (err) {
      // 409 — задача успела закончиться сама: строка обновится при следующем опросе.
      if (!(err instanceof ApiError && err.status === 409)) {
        setError(err instanceof ApiError ? err.message : "Не удалось остановить");
      }
    }
    setIsCancelling(false);
    onChanged?.();
  };

  const className = "flex min-w-0 flex-1 items-start gap-2 rounded-lg px-2 py-2 text-left hover:bg-white/5";
  return (
    <div className="flex items-start gap-1">
      {item.link ? (
        <Link to={item.link} onClick={onOpen} className={className}>
          {body}
        </Link>
      ) : (
        <div className={className}>{body}</div>
      )}
      {running && item.job_id && item.scope === "mine" && (
        <button
          type="button"
          onClick={() => void cancel()}
          disabled={isCancelling}
          aria-label="Остановить"
          title="Остановить — прервётся на ближайшем запросе к модели, сохранённое останется"
          className="mt-2 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-zinc-500 hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50"
        >
          {isCancelling ? <Loader2 size={12} className="animate-spin" /> : <X size={13} />}
        </button>
      )}
    </div>
  );
}

function Toast({ item, onClose }: { item: ActivityItem; onClose: () => void }) {
  const content = (
    <>
      <span className="mt-0.5">
        <StatusIcon item={item} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-xs font-medium text-zinc-100">
          {resultText(item)}: {item.subtitle && item.subtitle !== item.title ? item.subtitle : item.title}
        </span>
        {item.subtitle && item.subtitle !== item.title && (
          <span className="block truncate text-[11px] text-zinc-400">{item.title}</span>
        )}
        {item.message && <span className="mt-0.5 line-clamp-2 block text-[11px] text-zinc-500">{item.message}</span>}
      </span>
    </>
  );
  return (
    <div
      className={`flex items-start gap-1 rounded-xl border bg-zinc-900/95 p-1 shadow-xl shadow-black/40 backdrop-blur ${
        item.status === "error" ? "border-red-500/30" : "border-white/10"
      }`}
    >
      {item.link ? (
        <Link to={item.link} onClick={onClose} className="flex min-w-0 flex-1 items-start gap-2 rounded-lg p-2 hover:bg-white/5">
          {content}
        </Link>
      ) : (
        <div className="flex min-w-0 flex-1 items-start gap-2 p-2">{content}</div>
      )}
      <button
        type="button"
        onClick={onClose}
        aria-label="Закрыть"
        className="m-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
      >
        <X size={13} />
      </button>
    </div>
  );
}
