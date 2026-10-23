import { ChevronRight, Sparkles } from "lucide-react";
import { motion } from "motion/react";
import { useSyncExternalStore } from "react";
import { Link } from "react-router-dom";

import { WHATS_NEW } from "../../content/homeSlides";
import { SPRING_POP } from "../../utils/motion";

// «Что нового?» — не раздел меню, а объявление: блок в меню учётной записи и, в виде меню
// «шапка», плашка со светящимся контуром. Точка «не прочитано» горит, пока пользователь не
// открыл страницу нововведений после очередной выкладки (сравниваем id первой плитки).

export const WHATS_NEW_PATH = "/whats-new";
const SEEN_KEY = "whatsNew.seen";
const LATEST_ID = WHATS_NEW[0]?.id ?? "";

const listeners = new Set<() => void>();

function readSeen(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === LATEST_ID;
  } catch {
    return true;
  }
}

/** Вызывает страница «Что нового?» при открытии вкладки нововведений. */
export function markWhatsNewSeen() {
  try {
    localStorage.setItem(SEEN_KEY, LATEST_ID);
  } catch {
    // без хранилища точка просто не погаснет до перезагрузки
  }
  listeners.forEach((listener) => listener());
}

function useUnseen(): boolean {
  const seen = useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    readSeen,
  );
  return !seen;
}

function UnseenDot() {
  return (
    <motion.span
      initial={{ scale: 0 }}
      animate={{ scale: 1 }}
      transition={SPRING_POP}
      className="relative flex h-2 w-2"
      aria-label="Есть непрочитанные нововведения"
    >
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-60" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-rose-500" />
    </motion.span>
  );
}

const latest = WHATS_NEW[0];

/** Блок «Что нового?» в меню учётной записи (23.10.2026 — раньше строка внизу боковой
 * панели): значок в градиенте бренда, последнее нововведение и плашка «Новое», пока
 * страницу нововведений не открывали. */
export function WhatsNewMenuBlock({ onNavigate }: { onNavigate: () => void }) {
  const unseen = useUnseen();
  return (
    <Link
      to={`${WHATS_NEW_PATH}?tab=news`}
      onClick={onNavigate}
      className="nav-card group flex items-center gap-3 rounded-2xl p-3 transition-transform duration-200 active:scale-[0.98]"
    >
      <span className="brand-gradient relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-xl text-snow">
        <Sparkles size={17} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="text-sm font-semibold text-white">Что нового?</span>
          {unseen && (
            <span className="flex items-center gap-1.5 rounded-full border border-white/[0.08] bg-white/[0.05] px-2 py-0.5 text-[11px] font-medium text-zinc-200">
              <UnseenDot />
              Новое
            </span>
          )}
        </span>
        {latest && <span className="mt-0.5 line-clamp-2 text-xs text-zinc-500">{latest.title}</span>}
      </span>
      <ChevronRight
        size={16}
        className="shrink-0 text-zinc-600 transition-[color,transform] duration-200 group-hover:translate-x-0.5 group-hover:text-zinc-300"
      />
    </Link>
  );
}

/** Точка «есть непрочитанные нововведения» на аватаре — знак, что в меню есть что открыть. */
export function WhatsNewBadge() {
  return useUnseen() ? <UnseenDot /> : null;
}

/** Плашка в шапке со светящимся бегущим контуром. */
export function WhatsNewPill() {
  const unseen = useUnseen();
  return (
    <Link to={`${WHATS_NEW_PATH}?tab=news`} className="glow-ring group relative shrink-0 rounded-full">
      <span className="glow-ring__inner flex items-center gap-2 rounded-full bg-zinc-950 px-3.5 py-1.5 text-sm font-medium text-zinc-100 transition-colors group-hover:bg-zinc-900">
        <Sparkles size={14} className="text-indigo-300" />
        Что нового?
        {unseen && <UnseenDot />}
      </span>
    </Link>
  );
}
