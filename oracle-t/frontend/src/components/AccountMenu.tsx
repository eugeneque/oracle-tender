import {
  ArrowRight,
  Clock,
  LogOut,
  Settings,
  Star,
  Users as UsersIcon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Tender, TenderPage } from "../api/types";
import { useAuth } from "../context/useAuth";
import { daysLeft, percentValue, scoreBadgeClass } from "../utils/format";
import { DecisionMark } from "./DecisionMark";
import { UserAvatar } from "./UserAvatar";

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
export function AccountMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, right: 0 });
  const [favourites, setFavourites] = useState<Tender[] | null>(null);
  const [favouritesTotal, setFavouritesTotal] = useState(0);

  const openMenu = () => {
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      setPosition({ top: rect.bottom + 12, right: window.innerWidth - rect.right });
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

  const itemClass =
    "flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-zinc-300 hover:bg-white/[0.06]";

  return (
    <>
      <button
        ref={buttonRef}
        onClick={() => (isOpen ? setIsOpen(false) : openMenu())}
        title={user?.full_name}
        className={`rounded-full transition-all ${
          isOpen
            ? "shadow-[0_0_0_4px_rgba(99,102,241,0.25),0_0_24px_-2px_rgba(99,102,241,0.7)]"
            : "shadow-none ring-1 ring-white/10 hover:ring-white/20"
        }`}
      >
        <UserAvatar user={user} size="sm" />
      </button>

      {isOpen &&
        createPortal(
          <>
            {/* затемняет и блюрит всё, что находится позади меню — на уровне всего документа */}
            <div
              className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm"
              onClick={() => setIsOpen(false)}
            />

            <div
              className="fixed z-50 w-[360px]"
              style={{ top: position.top, right: position.right }}
            >
              {/* свечение по краям карточки */}
              <div className="absolute -inset-1 rounded-[20px] bg-gradient-to-br from-indigo-500/60 via-violet-500/25 to-transparent opacity-80 blur-lg" />

              <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-zinc-900/90 shadow-2xl backdrop-blur-2xl">
                {/* тонкая световая линия по верхней грани — эффект стекла */}
                <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/50 to-transparent" />

                <Link
                  to="/account"
                  onClick={() => setIsOpen(false)}
                  className="flex items-center gap-3 border-b border-white/[0.07] px-4 py-4 transition-colors hover:bg-white/[0.04]"
                  title="Настройки учётной записи"
                >
                  <UserAvatar user={user} size="lg" />
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-white">
                      {user?.full_name}
                    </div>
                    <div className="truncate text-xs text-zinc-500">@{user?.username}</div>
                    <div className="mt-1 inline-flex rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[11px] text-zinc-400">
                      {ROLE_LABELS[user?.role ?? ""] ?? user?.role}
                    </div>
                  </div>
                  <ArrowRight size={15} className="shrink-0 text-zinc-600" />
                </Link>

                {/* Избранное: то, к чему обещали вернуться, — ближе всего к руке. */}
                <div className="border-b border-white/[0.07] px-2 pb-1.5 pt-2">
                  <div className="flex items-center justify-between px-2 pb-1">
                    <span className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
                      <Star size={11} className="text-amber-400" fill="currentColor" />
                      Избранное
                      {favouritesTotal > 0 && (
                        <span className="text-zinc-600">{favouritesTotal}</span>
                      )}
                    </span>
                    {favouritesTotal > 0 && (
                      <Link
                        to="/tenders?favourites=1"
                        onClick={() => setIsOpen(false)}
                        className="text-[11px] text-indigo-400 hover:text-indigo-300"
                      >
                        Все →
                      </Link>
                    )}
                  </div>
                  {favourites === null ? (
                    <div className="px-2 py-1.5 text-xs text-zinc-600">Загрузка…</div>
                  ) : favourites.length === 0 ? (
                    <div className="px-2 py-1.5 text-xs leading-relaxed text-zinc-600">
                      Пока пусто. Отметьте закупку звёздочкой в карточке — и она появится
                      здесь.
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
                              className="block rounded-lg px-2 py-1.5 hover:bg-white/[0.06]"
                            >
                              <div className="flex items-start gap-2">
                                <DecisionMark decision={tender.ai_decision} size="sm" />
                                <span className="line-clamp-2 flex-1 text-xs leading-snug text-zinc-200">
                                  {tender.title}
                                </span>
                                <span
                                  className={`shrink-0 rounded border px-1 text-[10px] ${scoreBadgeClass(score)}`}
                                >
                                  {score === null ? "—" : `${score}%`}
                                </span>
                              </div>
                              <div className="mt-0.5 flex items-center gap-2 text-[11px] text-zinc-500">
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

                <div className="p-1.5">
                  <Link to="/account" onClick={() => setIsOpen(false)} className={itemClass}>
                    <Settings size={15} />
                    Настройки учётной записи
                  </Link>
                  {user?.role === "admin" && (
                    <Link to="/users" onClick={() => setIsOpen(false)} className={itemClass}>
                      <UsersIcon size={15} />
                      Пользователи
                    </Link>
                  )}
                </div>

                <div className="border-t border-white/[0.07] p-1.5">
                  <button
                    onClick={handleLogout}
                    className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm text-red-400 hover:bg-red-500/10"
                  >
                    <LogOut size={15} />
                    Выйти
                  </button>
                </div>
              </div>
            </div>
          </>,
          document.body,
        )}
    </>
  );
}
