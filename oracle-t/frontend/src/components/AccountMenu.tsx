import { LogOut, Settings, Users as UsersIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useNavigate } from "react-router-dom";

import { useAuth } from "../context/useAuth";

function initials(fullName: string | undefined): string {
  if (!fullName) return "?";
  const parts = fullName.trim().split(/\s+/);
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

export function AccountMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [position, setPosition] = useState({ top: 0, right: 0 });

  const openMenu = () => {
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      setPosition({ top: rect.bottom + 12, right: window.innerWidth - rect.right });
    }
    setIsOpen(true);
  };

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

  return (
    <>
      <button
        ref={buttonRef}
        onClick={() => (isOpen ? setIsOpen(false) : openMenu())}
        className={`flex h-9 w-9 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 text-xs font-semibold text-white transition-all ${
          isOpen
            ? "shadow-[0_0_0_4px_rgba(99,102,241,0.25),0_0_24px_-2px_rgba(99,102,241,0.7)]"
            : "shadow-none ring-1 ring-white/10 hover:ring-white/20"
        }`}
      >
        {initials(user?.full_name)}
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
              className="fixed z-50 w-72"
              style={{ top: position.top, right: position.right }}
            >
              {/* свечение по краям карточки */}
              <div className="absolute -inset-1 rounded-[20px] bg-gradient-to-br from-indigo-500/60 via-violet-500/25 to-transparent opacity-80 blur-lg" />

              <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-zinc-900/90 shadow-2xl backdrop-blur-2xl">
                {/* тонкая световая линия по верхней грани — эффект стекла */}
                <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-white/50 to-transparent" />

                <div className="flex items-center gap-3 border-b border-white/[0.07] px-4 py-4">
                  <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 text-sm font-semibold text-white">
                    {initials(user?.full_name)}
                  </span>
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-white">
                      {user?.full_name}
                    </div>
                    <div className="truncate text-xs text-zinc-500">@{user?.username}</div>
                  </div>
                </div>

                <div className="p-1.5">
                  {user?.role === "admin" && (
                    <Link
                      to="/users"
                      onClick={() => setIsOpen(false)}
                      className="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-zinc-300 hover:bg-white/[0.06]"
                    >
                      <UsersIcon size={15} />
                      Пользователи
                    </Link>
                  )}
                  <span
                    className="flex cursor-default items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-zinc-600"
                    title="Появится в следующих этапах разработки"
                  >
                    <Settings size={15} />
                    Настройки
                  </span>
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
