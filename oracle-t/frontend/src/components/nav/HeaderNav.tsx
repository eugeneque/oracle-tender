import { ArrowRight, ChevronDown } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { WHATS_NEW } from "../../content/homeSlides";
import type { Slide, SlideTone } from "../../content/homeSlides";
import { useAuth } from "../../context/useAuth";
import { SPRING_SNAPPY, dropdownVariants, staggerContainer, staggerItem } from "../../utils/motion";
import { NAV_GROUPS, isNavItemActive, visibleNavItems } from "./navConfig";
import type { NavGroupKey, NavItem } from "./navConfig";
import { WHATS_NEW_PATH } from "./WhatsNew";

// Меню в шапке: группы разделов с выпадающими подпунктами (по мотивам мега-меню Flowbase).
// Открывается наведением (с задержкой, чтобы не мигать при проходе мышью) и кликом;
// закрывается уходом мыши, Escape, кликом вне меню и переходом на другую страницу.

const OPEN_DELAY_MS = 80;
const CLOSE_DELAY_MS = 160;
const NEWS_PER_GROUP = 3;

const TONE_CLASS: Record<SlideTone, string> = {
  indigo: "bg-indigo-500/15 text-indigo-300",
  sky: "bg-sky-500/15 text-sky-300",
  emerald: "bg-emerald-500/15 text-emerald-300",
  amber: "bg-amber-500/15 text-amber-300",
  violet: "bg-violet-500/15 text-violet-300",
  rose: "bg-rose-500/15 text-rose-300",
};

/** Свежие нововведения, которые ведут в разделы группы, — правая колонка выпадающего меню. */
function groupNews(items: NavItem[], isAdmin: boolean): Slide[] {
  const paths = new Set(items.map((item) => item.to.split("?")[0]));
  return WHATS_NEW.filter((slide) => (!slide.adminOnly || isAdmin) && slide.link && paths.has(slide.link.to)).slice(
    0,
    NEWS_PER_GROUP,
  );
}

export function HeaderNav() {
  const { user } = useAuth();
  const { pathname, search } = useLocation();
  const [open, setOpen] = useState<NavGroupKey | null>(null);
  const [hovered, setHovered] = useState<NavGroupKey | null>(null);
  const timer = useRef<number>();
  const rootRef = useRef<HTMLElement>(null);

  const items = visibleNavItems(user);
  const groups = NAV_GROUPS.map((group) => ({
    ...group,
    items: items.filter((item) => item.group === group.key),
  })).filter((group) => group.items.length > 0);

  useEffect(() => setOpen(null), [pathname]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(null);
    const onClick = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(null);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  const schedule = (next: NavGroupKey | null, delay: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(next), delay);
  };

  return (
    <nav
      ref={rootRef}
      className="flex items-center gap-0.5"
      onMouseLeave={() => {
        setHovered(null);
        schedule(null, CLOSE_DELAY_MS);
      }}
    >
      {groups.map((group) => {
        const isActive = group.items.some((item) => isNavItemActive(item, pathname, search));
        const isOpen = open === group.key;
        return (
          <div
            key={group.key}
            className="relative"
            onMouseEnter={() => {
              setHovered(group.key);
              schedule(group.key, open ? 0 : OPEN_DELAY_MS);
            }}
          >
            <button
              type="button"
              aria-expanded={isOpen}
              aria-haspopup="menu"
              onClick={() => {
                window.clearTimeout(timer.current);
                setOpen(isOpen ? null : group.key);
              }}
              className={`relative flex items-center gap-1 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                isActive || isOpen ? "text-white" : "text-zinc-400 hover:text-zinc-100"
              }`}
            >
              {hovered === group.key && (
                <motion.span
                  layoutId="header-nav-hover"
                  className="absolute inset-0 rounded-lg bg-white/[0.06]"
                  transition={SPRING_SNAPPY}
                />
              )}
              <span className="relative">{group.label}</span>
              <motion.span
                className="relative text-zinc-500"
                animate={{ rotate: isOpen ? 180 : 0 }}
                transition={SPRING_SNAPPY}
              >
                <ChevronDown size={14} />
              </motion.span>
              {isActive && (
                <motion.span
                  layoutId="header-nav-active"
                  className="absolute inset-x-3 -bottom-[13px] h-0.5 rounded-full bg-indigo-400"
                  transition={SPRING_SNAPPY}
                />
              )}
            </button>

            <AnimatePresence>
              {isOpen && (
                <motion.div
                  role="menu"
                  variants={dropdownVariants}
                  initial="initial"
                  animate="animate"
                  exit="exit"
                  style={{ transformOrigin: "top left" }}
                  className="absolute left-0 top-full z-50 pt-3"
                >
                  <DropdownPanel
                    items={group.items}
                    news={groupNews(group.items, user?.role === "admin")}
                    pathname={pathname}
                    search={search}
                  />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}
    </nav>
  );
}

function DropdownPanel({
  items,
  news,
  pathname,
  search,
}: {
  items: NavItem[];
  news: Slide[];
  pathname: string;
  search: string;
}) {
  return (
    <div className="relative flex overflow-hidden rounded-2xl border border-white/10 bg-zinc-900/95 shadow-2xl shadow-black/40 backdrop-blur-xl">
      {/* световая линия по верхней грани — как у меню учётной записи */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/40 to-transparent" />
      <motion.ul
        variants={staggerContainer}
        initial="initial"
        animate="animate"
        className="flex w-[320px] shrink-0 flex-col gap-0.5 p-2"
      >
        {items.map((item) => {
          const Icon = item.icon;
          const isActive = isNavItemActive(item, pathname, search);
          return (
            <motion.li key={item.to} variants={staggerItem}>
              <Link
                role="menuitem"
                to={item.to}
                className={`group flex items-center gap-3 rounded-xl px-2.5 py-2 transition-colors ${
                  isActive ? "bg-white/[0.07]" : "hover:bg-white/[0.05]"
                }`}
              >
                <span
                  className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border transition-colors ${
                    isActive
                      ? "border-indigo-400/30 bg-indigo-500/15 text-indigo-300"
                      : "border-white/[0.08] bg-white/[0.04] text-zinc-400 group-hover:text-zinc-100"
                  }`}
                >
                  <Icon size={17} />
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-zinc-100">{item.label}</span>
                  <span className="block truncate text-xs text-zinc-500">{item.hint}</span>
                </span>
              </Link>
            </motion.li>
          );
        })}
      </motion.ul>

      {news.length > 0 && (
        <motion.div
          variants={staggerContainer}
          initial="initial"
          animate="animate"
          className="w-[300px] border-l border-white/[0.06] bg-indigo-500/[0.05] p-2"
        >
          <div className="flex items-center justify-between px-2.5 pb-1 pt-1.5">
            <span className="text-xs font-medium text-zinc-400">Новое в разделе</span>
            <Link
              to={`${WHATS_NEW_PATH}?tab=news`}
              className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300"
            >
              Все <ArrowRight size={12} />
            </Link>
          </div>
          {news.map((slide) => {
            const Icon = slide.icon;
            return (
              <motion.div key={slide.id} variants={staggerItem}>
                <Link
                  to={`${WHATS_NEW_PATH}?tab=news`}
                  className="flex items-center gap-3 rounded-xl px-2.5 py-2 transition-colors hover:bg-white/[0.05]"
                >
                  <span
                    className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${TONE_CLASS[slide.tone]}`}
                  >
                    <Icon size={16} />
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-zinc-100">{slide.tile}</span>
                    <span className="block truncate text-xs text-zinc-500">{slide.kicker}</span>
                  </span>
                </Link>
              </motion.div>
            );
          })}
        </motion.div>
      )}
    </div>
  );
}
