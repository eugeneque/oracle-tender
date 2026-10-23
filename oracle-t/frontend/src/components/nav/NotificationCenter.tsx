import { AlertCircle, AlertTriangle, ArrowRight, Bell, CheckCircle2, Clock, Loader2, Volume2, VolumeX, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";

import { ACTIVITY_EVENT, ApiError, api } from "../../api/client";
import type { ActivityFeed, ActivityItem } from "../../api/types";
import { dropdownVariants } from "../../utils/motion";
import { Mira, moodFromProgress } from "../mira/Mira";
import type { MiraMood } from "../mira/Mira";
import { StepProgress } from "../ui/StepProgress";
import { summarizeActivity } from "./activitySummary";
import type { ActivitySummary } from "./activitySummary";

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
  // Полный разбор «успешен», даже если два шага из трёх упали на ключе модели — зелёная
  // галочка с таким итогом вводила в заблуждение (23.10.2026).
  if (summarizeActivity(item.message)?.tone === "error") return "Готово с ошибками";
  return "Готово";
}

/** Разбор закупки — с лицом Миры: по нему видно и стадию, и итог. */
function miraMood(item: ActivityItem, summary: ActivitySummary | null): MiraMood {
  if (item.status === "running") return moodFromProgress(item.message);
  if (item.status === "error") return "error";
  if (item.status === "success") return summary?.tone === "error" ? "doubtful" : "happy";
  return "idle";
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
        className={`relative flex h-9 w-9 items-center justify-center rounded-xl transition-[color,background-color,transform] duration-200 active:scale-95 ${
          isOpen ? "nav-pill text-white" : "text-zinc-400 hover:bg-white/[0.05] hover:text-zinc-100"
        }`}
      >
        {active.length > 0 ? <Loader2 size={17} className="animate-spin text-indigo-300" /> : <Bell size={17} />}
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

      {/* Панель — в стиле боковой панели (23.10.2026): то же стекло, что у меню учётной
          записи, строки со скруглением и подписи разделов без капслока. */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            variants={dropdownVariants}
            initial="initial"
            animate="animate"
            exit="exit"
            style={{ transformOrigin: "top right" }}
            className="nav-popover absolute right-0 top-12 z-50 w-[24rem] max-w-[calc(100vw-2rem)] overflow-hidden rounded-[22px] p-2"
          >
            <div className="flex items-center justify-between gap-3 px-3 pb-2 pt-1.5">
              <div className="min-w-0">
                <div className="text-[15px] font-semibold text-white">Уведомления</div>
                <div className="text-xs text-zinc-500">Ваши процессы и итоги за сутки</div>
              </div>
              <button
                type="button"
                onClick={toggleMute}
                title={muted ? "Включить звук" : "Выключить звук"}
                aria-label={muted ? "Включить звук" : "Выключить звук"}
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-zinc-500 transition-[color,background-color,transform] duration-200 hover:bg-white/[0.05] hover:text-zinc-100 active:scale-95"
              >
                {muted ? <VolumeX size={16} /> : <Volume2 size={16} />}
              </button>
            </div>
            <div className="mx-2 h-px bg-white/[0.06]" />
            <div className="max-h-[65vh] overflow-y-auto pt-1">
              {items.length === 0 && (
                <div className="flex flex-col items-center gap-3 px-6 py-8 text-center">
                  <span className="nav-well flex h-12 w-12 items-center justify-center rounded-2xl text-zinc-500">
                    <Bell size={18} />
                  </span>
                  <p className="text-xs leading-relaxed text-zinc-500">
                    Здесь появятся запущенные вами процессы и их итоги — разбор закупки, опрос
                    площадок, опрос каталога.
                  </p>
                </div>
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
          </motion.div>
        )}
      </AnimatePresence>

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

function SectionTitle({ children }: { children: ReactNode }) {
  return <p className="px-3 pb-1.5 pt-3 text-xs font-medium text-zinc-500">{children}</p>;
}

function StatusIcon({ item }: { item: ActivityItem }) {
  if (item.status === "running") return <Loader2 size={14} className="animate-spin text-indigo-300" />;
  if (item.status === "queued") return <Clock size={14} className="text-zinc-500" />;
  if (item.status === "error") return <AlertCircle size={14} className="text-red-400" />;
  if (item.status === "cancelled") return <X size={14} className="text-zinc-500" />;
  if (summarizeActivity(item.message)?.tone === "error") return <AlertTriangle size={14} className="text-amber-400" />;
  return <CheckCircle2 size={14} className="text-emerald-400" />;
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

  const summary = running ? null : summarizeActivity(item.message);
  const isTender = item.link?.startsWith("/tenders/") ?? false;
  const dot = { ok: "bg-emerald-400", warn: "bg-amber-400", error: "bg-red-400" } as const;

  const body = (
    <>
      {isTender ? (
        <span className="flex h-9 w-9 shrink-0 items-center justify-center">
          <Mira size={30} mood={miraMood(item, summary)} busy={item.status === "running"} />
        </span>
      ) : (
        <span className="nav-well flex h-9 w-9 shrink-0 items-center justify-center rounded-xl">
          <StatusIcon item={item} />
        </span>
      )}
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate text-[13px] font-medium text-zinc-100">{item.title}</span>
          {isNew && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-400" />}
        </span>
        {meta && <span className="block truncate text-[11px] text-zinc-500">{meta}{percent}</span>}
        {running ? (
          <>
            {item.message && (
              <span className="mt-1 block truncate text-[11px] text-indigo-300/90">{item.message}</span>
            )}
            <StepProgress
              text={item.message}
              value={item.status === "queued" ? 0 : item.progress}
              className="mt-2"
            />
          </>
        ) : summary ? (
          // Итог полного разбора — по шагам, с точкой-светофором; целиком — в подсказке.
          <span className="mt-1.5 flex flex-col gap-1" title={item.message ?? undefined}>
            <span
              className={`text-[11px] font-medium ${
                summary.tone === "error" ? "text-amber-300" : "text-emerald-300"
              }`}
            >
              {resultText(item)}
            </span>
            {summary.steps.map((step) => (
              <span key={step.label + step.text} className="flex min-w-0 items-center gap-2 text-[11px]">
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot[step.tone]}`} />
                {step.label && <span className="w-14 shrink-0 text-zinc-500">{step.label}</span>}
                <span className="truncate text-zinc-300">{step.text}</span>
              </span>
            ))}
          </span>
        ) : (
          item.message && (
            <span
              className={`mt-0.5 line-clamp-2 text-[11px] ${
                item.status === "error" ? "text-red-300/90" : "text-zinc-400"
              }`}
            >
              {`${resultText(item)}: ${item.message}`}
            </span>
          )
        )}
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

  const className =
    "flex min-w-0 flex-1 items-start gap-3 rounded-2xl px-2.5 py-2.5 text-left transition-[background-color,transform] duration-200 hover:bg-white/[0.05] active:scale-[0.99]";
  return (
    <div>
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
            className="mt-2.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-red-500/10 hover:text-red-300 disabled:opacity-50"
          >
            {isCancelling ? <Loader2 size={12} className="animate-spin" /> : <X size={13} />}
          </button>
        )}
      </div>
      {/* Общая причина сбоя — одной плашкой, а не повтором в каждом шаге; ведёт туда, где
          её исправляют. Вне ссылки строки: ссылка в ссылке недопустима. */}
      {summary?.alert && (
        <div className="mb-1 ml-[58px] mr-2 flex items-start gap-2 rounded-xl border border-red-500/20 bg-red-500/[0.07] px-2.5 py-2 text-[11px] leading-snug text-red-200">
          <AlertCircle size={13} className="mt-px shrink-0 text-red-400" />
          <span className="min-w-0 flex-1">
            {summary.alert}
            {summary.alert.includes("Модели ИИ") && (
              <Link to="/ai" onClick={onOpen} className="mt-1 flex items-center gap-1 font-medium text-red-300 hover:text-red-100">
                Открыть «Модели ИИ»
                <ArrowRight size={11} />
              </Link>
            )}
          </span>
        </div>
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
        {item.message && <span className="mt-0.5 line-clamp-2 text-[11px] text-zinc-500">{item.message}</span>}
      </span>
    </>
  );
  return (
    <div
      className={`nav-popover flex items-start gap-1 rounded-2xl p-1 ${
        item.status === "error" ? "ring-1 ring-red-500/30" : ""
      }`}
    >
      {item.link ? (
        <Link to={item.link} onClick={onClose} className="flex min-w-0 flex-1 items-start gap-2 rounded-xl p-2 transition-colors hover:bg-white/[0.05]">
          {content}
        </Link>
      ) : (
        <div className="flex min-w-0 flex-1 items-start gap-2 p-2">{content}</div>
      )}
      <button
        type="button"
        onClick={onClose}
        aria-label="Закрыть"
        className="m-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-zinc-500 transition-colors hover:bg-white/[0.05] hover:text-zinc-200"
      >
        <X size={13} />
      </button>
    </div>
  );
}
