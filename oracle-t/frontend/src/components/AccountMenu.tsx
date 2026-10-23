import {
  ArrowRight,
  Clock,
  History,
  LogOut,
  MoreVertical,
  Settings,
  Star,
  SunMoon,
  Users as UsersIcon,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Tender, TenderPage } from "../api/types";
import { useAuth } from "../context/useAuth";
import { daysLeft, percentValue } from "../utils/format";
import { dropdownVariants, dropupVariants, scrimVariants } from "../utils/motion";
import { DecisionMark } from "./DecisionMark";
import { ThemeToggle } from "./ThemeToggle";
import { UserAvatar } from "./UserAvatar";
import { ScoreBadge } from "./ui/ScoreBadge";
import { WhatsNewBadge, WhatsNewMenuBlock } from "./nav/WhatsNew";

// Сколько отложенных закупок показывать в меню: это напоминание «к чему вернуться»,
// а не список — за списком есть раздел «Избранное» на странице тендеров.
const FAVOURITES_PREVIEW = 5;

const ROLE_LABELS: Record<string, string> = {
  admin: "Администратор",
  user: "Пользователь",
};

/**
 * Меню учётной записи (расширено 17.09.2026). Раньше — два пункта и «Выйти»; теперь оно
 * ещё и место, куда возвращаются: сверху отложенные закупки, затем настройки своей
 * учётной записи (имя, аватар) и раздел администратора.
 */
/**
 * `variant="sidebar"` (23.10.2026) — карточка учётной записи внизу боковой панели: аватар,
 * имя и логин, меню раскрывается вверх от неё. В свёрнутой панели остаётся один аватар.
 */
export function AccountMenu({
  variant = "avatar",
  collapsed = false,
}: {
  variant?: "avatar" | "sidebar";
  collapsed?: boolean;
}) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [position, setPosition] = useState<CSSProperties>({});
  const [favourites, setFavourites] = useState<Tender[] | null>(null);
  const [favouritesTotal, setFavouritesTotal] = useState(0);
  const inSidebar = variant === "sidebar";

  const openMenu = () => {
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      setPosition(
        inSidebar
          ? {
              bottom: window.innerHeight - rect.top + 10,
              left: rect.left,
              maxHeight: rect.top - 22,
              transformOrigin: "bottom left",
            }
          : {
              top: rect.bottom + 12,
              right: window.innerWidth - rect.right,
              maxHeight: window.innerHeight - rect.bottom - 24,
              transformOrigin: "top right",
            },
      );
    }
    setIsOpen(true);
  };

  // Избранное перечитывается при каждом открытии: звёздочку могли поставить в карточке
  // секунду назад, и меню с устаревшим списком выглядело бы сломанным.
  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    void api
      .get<TenderPage>(
        `/tenders?bookmarked=true&limit=${FAVOURITES_PREVIEW}&sort_by=application_end&sort_dir=asc`,
      )
      .then((page) => {
        if (cancelled) return;
        setFavourites(page.items);
        setFavouritesTotal(page.total);
      })
      .catch(() => {
        if (!cancelled) setFavourites([]);
      });
    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOpen]);

  const handleLogout = async () => {
    setIsOpen(false);
    await logout();
    navigate("/login");
  };

  // Пункты меню — в стиле боковой панели (23.10.2026): высокие строки со скруглением,
  // приглушённая иконка, которая светлеет при наведении, и лёгкое «нажатие».
  const itemClass =
    "group/item flex h-10 items-center gap-3 rounded-xl px-3 text-sm font-medium text-zinc-300 transition-[color,background-color,transform] duration-200 hover:bg-white/[0.05] hover:text-white active:scale-[0.98]";
  const itemIconClass = "shrink-0 text-zinc-500 transition-colors group-hover/item:text-zinc-200";
  const divider = <div className="mx-2 my-2 h-px bg-white/[0.06]" />;

  const toggleMenu = () => (isOpen ? setIsOpen(false) : openMenu());

  return (
    <>
      {inSidebar && !collapsed ? (
        <button
          ref={buttonRef}
          onClick={toggleMenu}
          title={user?.full_name}
          className={`nav-card group flex min-w-0 flex-1 items-center gap-3 rounded-2xl py-1.5 pl-1.5 pr-1.5 text-left transition-transform duration-200 active:scale-[0.98] ${
            isOpen ? "nav-card--open" : ""
          }`}
        >
          <span className="relative shrink-0">
            <UserAvatar user={user} size="md" />
            <span className="absolute -right-0.5 -top-0.5">
              <WhatsNewBadge />
            </span>
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold text-white">{user?.full_name}</span>
            <span className="block truncate text-xs text-zinc-500">@{user?.username}</span>
          </span>
          <span
            className={`shrink-0 rounded-lg p-1 transition-colors ${
              isOpen ? "bg-white/[0.08] text-zinc-100" : "text-zinc-500 group-hover:text-zinc-200"
            }`}
          >
            <MoreVertical size={16} />
          </span>
        </button>
      ) : (
        <button
          ref={buttonRef}
          onClick={toggleMenu}
          title={user?.full_name}
          className={`relative rounded-full transition-all duration-200 active:scale-95 ${
            isOpen ? "ring-2 ring-white/20" : "ring-1 ring-white/10 hover:ring-white/20"
          }`}
        >
          <UserAvatar user={user} size={inSidebar ? "md" : "sm"} />
          <span className="absolute -right-0.5 -top-0.5">
            <WhatsNewBadge />
          </span>
        </button>
      )}

      {createPortal(
        <AnimatePresence>
          {isOpen && (
            <motion.div key="scrim" variants={scrimVariants} initial="initial" animate="animate" exit="exit">
            {/* затемняет и блюрит всё, что находится позади меню — на уровне всего документа */}
            <div
              className="fixed inset-0 z-40 bg-scrim/50 backdrop-blur-sm"
              onClick={() => setIsOpen(false)}
            />
            </motion.div>
          )}
          {isOpen && (
            <motion.div
              key="menu"
              variants={inSidebar ? dropupVariants : dropdownVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="nav-popover fixed z-50 w-[360px] overflow-y-auto rounded-[22px] p-2"
              style={position}
            >
              <Link
                to="/account"
                onClick={() => setIsOpen(false)}
                className="group flex items-center gap-3 rounded-2xl p-3 transition-[background-color,transform] duration-200 hover:bg-white/[0.04] active:scale-[0.98]"
                title="Настройки учётной записи"
              >
                <UserAvatar user={user} size="lg" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[15px] font-semibold text-white">{user?.full_name}</div>
                  <div className="truncate text-xs text-zinc-500">@{user?.username}</div>
                  <div className="mt-1.5 inline-flex rounded-full border border-white/[0.08] bg-white/[0.05] px-2 py-0.5 text-[11px] font-medium text-zinc-300">
                    {ROLE_LABELS[user?.role ?? ""] ?? user?.role}
                  </div>
                </div>
                <ArrowRight
                  size={16}
                  className="shrink-0 text-zinc-600 transition-[color,transform] duration-200 group-hover:translate-x-0.5 group-hover:text-zinc-300"
                />
              </Link>

              {/* «Что нового?» переехал сюда из низа боковой панели (23.10.2026). */}
              <div className="mt-1">
                <WhatsNewMenuBlock onNavigate={() => setIsOpen(false)} />
              </div>

              {divider}

              {/* Избранное: то, к чему обещали вернуться, — ближе всего к руке. */}
              <div>
                <div className="flex items-center justify-between px-3 pb-1.5">
                  <span className="flex items-center gap-1.5 text-xs font-medium text-zinc-500">
                    <Star size={12} className="text-amber-400" fill="currentColor" />
                    Избранное
                    {favouritesTotal > 0 && <span className="text-zinc-600">{favouritesTotal}</span>}
                  </span>
                  {favouritesTotal > 0 && (
                    <Link
                      to="/tenders?favourites=1"
                      onClick={() => setIsOpen(false)}
                      className="group flex items-center gap-1 text-xs font-medium text-indigo-400 hover:text-indigo-300"
                    >
                      Все
                      <ArrowRight size={12} className="transition-transform group-hover:translate-x-0.5" />
                    </Link>
                  )}
                </div>
                {favourites === null ? (
                  <div className="px-3 py-1.5 text-xs text-zinc-600">Загрузка…</div>
                ) : favourites.length === 0 ? (
                  <div className="px-3 py-1.5 text-xs leading-relaxed text-zinc-600">
                    Пока пусто. Отметьте закупку звёздочкой в карточке — и она появится здесь.
                  </div>
                ) : (
                  <ul className="flex flex-col gap-0.5">
                    {favourites.map((tender) => {
                      const left = daysLeft(tender.application_end);
                      const score = percentValue(tender.ai_score);
                      return (
                        <li key={tender.id}>
                          <Link
                            to={`/tenders/${tender.id}`}
                            onClick={() => setIsOpen(false)}
                            className="group block rounded-2xl px-3 py-2.5 transition-[background-color,transform] duration-200 hover:bg-white/[0.05] active:scale-[0.98]"
                          >
                            <div className="flex items-start gap-2">
                              <DecisionMark decision={tender.ai_decision} size="sm" />
                              <span className="line-clamp-2 flex-1 text-[13px] font-medium leading-snug text-zinc-200 group-hover:text-white">
                                {tender.title}
                              </span>
                              <ScoreBadge score={score} size="xs" />
                            </div>
                            <div className="mt-1 flex items-center gap-2 pl-7 text-[11px] text-zinc-500">
                              <span className="truncate">{tender.customer_name ?? tender.source.name}</span>
                              {left !== null && (
                                <span
                                  className={`flex shrink-0 items-center gap-1 ${left <= 5 && left >= 0 ? "text-red-400" : ""}`}
                                >
                                  <Clock size={10} />
                                  {left >= 0 ? `${left} дн.` : "срок истёк"}
                                </span>
                              )}
                            </div>
                          </Link>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>

              {divider}

              <div className="flex flex-col gap-0.5">
                <Link to="/account" onClick={() => setIsOpen(false)} className={itemClass}>
                  <Settings size={17} className={itemIconClass} />
                  Настройки учётной записи
                </Link>
                <Link to="/changelog" onClick={() => setIsOpen(false)} className={itemClass}>
                  <History size={17} className={itemIconClass} />
                  История обновлений
                </Link>
                {user?.role === "admin" && (
                  <Link to="/users" onClick={() => setIsOpen(false)} className={itemClass}>
                    <UsersIcon size={17} className={itemIconClass} />
                    Пользователи
                  </Link>
                )}
                {/* Тема — здесь, а не в шапке (14.10.2026): её переключают редко, а место в
                    шапке нужнее поиску. */}
                <div className="flex h-10 items-center gap-3 px-3 text-sm font-medium text-zinc-300">
                  <SunMoon size={17} className="shrink-0 text-zinc-500" />
                  <span className="flex-1">Тема</span>
                  <ThemeToggle />
                </div>
              </div>

              {divider}

              <button
                onClick={handleLogout}
                className="group/item flex h-10 w-full items-center gap-3 rounded-xl px-3 text-left text-sm font-medium text-red-400 transition-[background-color,transform] duration-200 hover:bg-red-500/10 active:scale-[0.98]"
              >
                <LogOut size={17} className="shrink-0" />
                Выйти
              </button>
            </motion.div>
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}
