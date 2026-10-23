import { ChevronRight, Sparkles } from "lucide-react";
import { motion } from "motion/react";
import { useSyncExternalStore } from "react";
import { Link } from "react-router-dom";

import { CHANGELOG } from "../../content/changelog";
import { WHATS_NEW } from "../../content/homeSlides";
import { SPRING_POP } from "../../utils/motion";

// «Что нового?» — не раздел меню, а объявление: строка в меню учётной записи и, в виде меню
// «шапка», плашка со светящимся контуром. Пока пользователь не открыл страницу нововведений
// в текущей версии приложения, строка мягко подсвечена, а на аватаре горит точка.
// Версия — верхняя выкладка журнала изменений (24.10.2026; раньше — id первой плитки, и
// выкладка без новой плитки подсветку не возвращала).

export const WHATS_NEW_PATH = "/whats-new";
const SEEN_KEY = "whatsNew.seen";
const APP_VERSION = CHANGELOG[0] ? `${CHANGELOG[0].date}:${CHANGELOG[0].title}` : "";

const listeners = new Set<() => void>();

function readSeen(): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === APP_VERSION;
  } catch {
    return true;
  }
}

/** Вызывает страница «Что нового?» при открытии вкладки нововведений. */
export function markWhatsNewSeen() {
  try {
    localStorage.setItem(SEEN_KEY, APP_VERSION);
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

// Точка спокойная (24.10.2026): акцентная, с мягким ореолом и медленным «дыханием» вместо
// красной вспышки — это приглашение, а не тревога.
function UnseenDot() {
  return (
    <motion.span
      initial={{ scale: 0 }}
      animate={{ scale: 1 }}
      transition={SPRING_POP}
      className="relative flex h-2 w-2"
      aria-label="Есть непрочитанные нововведения"
    >
      <span className="absolute -inset-1 animate-pulse rounded-full bg-indigo-400/30 blur-[2px]" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-400 ring-2 ring-zinc-950" />
    </motion.span>
  );
}

const latest = WHATS_NEW[0];

/** «Что нового?» в меню учётной записи. Редизайн 24.10.2026: та же строка, что и остальные
 * пункты меню (раньше — карточка с градиентным значком, выбивалась из меню). Пока текущую
 * версию не смотрели — мягкая акцентная подложка, подпись «Новое» и последнее нововведение;
 * после просмотра — обычный пункт. */
export function WhatsNewMenuBlock({ onNavigate }: { onNavigate: () => void }) {
  const unseen = useUnseen();
  return (
    <Link
      to={`${WHATS_NEW_PATH}?tab=news`}
      onClick={onNavigate}
      className={`group/item flex items-center gap-3 rounded-xl px-3 transition-[color,background-color,box-shadow,transform] duration-200 active:scale-[0.98] ${
        unseen
          ? "bg-indigo-500/[0.08] py-2.5 ring-1 ring-inset ring-indigo-400/20 hover:bg-indigo-500/[0.12]"
          : "h-10 text-zinc-300 hover:bg-white/[0.05] hover:text-white"
      }`}
    >
      <Sparkles
        size={17}
        className={`shrink-0 transition-colors ${
          unseen ? "text-indigo-300" : "text-zinc-500 group-hover/item:text-zinc-200"
        }`}
      />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className={`text-sm font-medium ${unseen ? "text-white" : ""}`}>Что нового?</span>
          {unseen && (
            <span className="flex items-center gap-1.5 text-[11px] font-medium text-indigo-300">
              <UnseenDot />
              Новое
            </span>
          )}
        </span>
        {unseen && latest && (
          <span className="mt-0.5 block truncate text-xs text-zinc-500">{latest.title}</span>
        )}
      </span>
      <ChevronRight
        size={15}
        className="shrink-0 text-zinc-600 transition-[color,transform] duration-200 group-hover/item:translate-x-0.5 group-hover/item:text-zinc-300"
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
