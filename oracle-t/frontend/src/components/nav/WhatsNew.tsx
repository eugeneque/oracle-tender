import { ArrowRight, Sparkles } from "lucide-react";
import { motion } from "motion/react";
import { useSyncExternalStore } from "react";
import { Link, useLocation } from "react-router-dom";

import { WHATS_NEW } from "../../content/homeSlides";
import { SPRING_POP } from "../../utils/motion";

// «Что нового?» — не раздел меню, а объявление: в боковой панели это карточка внизу, в
// шапке — плашка со светящимся контуром. Точка «не прочитано» горит, пока пользователь не
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

/** Карточка внизу боковой панели (по мотивам блока тарифа в SaaS-меню). Значок и кнопка —
 * в том же переливающемся градиенте, что блок бренда наверху панели. */
export function WhatsNewCard({ collapsed }: { collapsed: boolean }) {
  const unseen = useUnseen();
  const { pathname } = useLocation();
  const isActive = pathname === WHATS_NEW_PATH;

  if (collapsed) {
    return (
      <Link
        to={`${WHATS_NEW_PATH}?tab=news`}
        title="Что нового?"
        className="glow-ring relative mx-auto flex h-[52px] w-[52px] items-center justify-center rounded-xl"
      >
        <span className="glow-ring__inner flex h-full w-full items-center justify-center rounded-[11px] bg-zinc-950 text-indigo-300">
          <Sparkles size={17} />
        </span>
        {unseen && (
          <span className="absolute -right-0.5 -top-0.5">
            <UnseenDot />
          </span>
        )}
      </Link>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.25, type: "spring", bounce: 0.15, duration: 0.5 }}
      className={`relative overflow-hidden rounded-2xl border p-4 ${
        isActive ? "border-indigo-400/40" : "border-white/[0.08]"
      } bg-gradient-to-b from-indigo-500/[0.14] via-violet-500/[0.06] to-white/[0.02]`}
    >
      {/* мягкое пятно света в углу — как у карточки тарифа в референсе */}
      <div className="pointer-events-none absolute -right-10 -top-10 h-28 w-28 rounded-full bg-indigo-500/20 blur-2xl" />

      <div className="relative flex items-center gap-3">
        <span className="brand-gradient relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-xl text-snow">
          <Sparkles size={17} />
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-xs text-zinc-400">
            Что нового?
            {unseen && <UnseenDot />}
          </div>
          <div className="truncate text-sm font-semibold text-white">
            {latest ? latest.kicker : "Sova"}
          </div>
        </div>
      </div>

      {latest && (
        <p className="relative mt-3 line-clamp-3 text-xs leading-relaxed text-zinc-400">{latest.title}</p>
      )}

      <Link
        to={`${WHATS_NEW_PATH}?tab=news`}
        className="brand-gradient group relative mt-3 flex items-center justify-center gap-2 overflow-hidden rounded-xl px-3 py-2.5 text-sm font-medium text-snow transition-[filter,transform] hover:brightness-110 active:scale-[0.97]"
      >
        Посмотреть
        <ArrowRight size={15} className="transition-transform group-hover:translate-x-0.5" />
      </Link>
    </motion.div>
  );
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
