import {
  BarChart3,
  Boxes,
  Building2,
  ChevronsLeft,
  ChevronsRight,
  KanbanSquare,
  LayoutDashboard,
  Plug,
  Search,
  Settings,
  Users as UsersIcon,
} from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";

import { useAuth } from "../context/useAuth";
import { AccountMenu } from "./AccountMenu";
import { Logo, LogoMark } from "./Logo";

interface NavItem {
  label: string;
  to: string;
  icon: typeof LayoutDashboard;
  adminOnly?: boolean;
  disabled?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { label: "Дашборд", to: "/", icon: LayoutDashboard },
  { label: "Тендеры", to: "/tenders", icon: KanbanSquare },
  { label: "Аналитика", to: "/analytics", icon: BarChart3 },
  { label: "Каталог продукции", to: "/catalog", icon: Boxes },
  { label: "Моя компания", to: "/company", icon: Building2 },
  { label: "Пользователи", to: "/users", icon: UsersIcon, adminOnly: true },
  { label: "Интеграции", to: "/integrations", icon: Plug },
  { label: "Настройки", to: "/settings", icon: Settings },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const location = useLocation();
  const [isCollapsed, setIsCollapsed] = useState(false);

  // `h-screen`, а не `min-h-screen`: страницам нужен предсказуемый потолок высоты, чтобы
  // внутри них скроллились отдельные панели, а не весь документ — иначе в двухпанельном
  // режиме приходится сначала пролистать страницу и только потом карточку. Страницы, которым
  // нужен обычный скролл, прокручивают свой контейнер (`overflow-y-auto` ниже).
  return (
    <div className="flex h-screen bg-zinc-950">
      <aside
        className={`flex h-screen shrink-0 flex-col border-r border-white/[0.06] bg-zinc-950 transition-all duration-200 ${
          isCollapsed ? "w-[76px]" : "w-[260px]"
        }`}
      >
        <div className="flex h-16 items-center justify-between px-4">
          {isCollapsed ? <LogoMark size={22} /> : <Logo size={26} />}
          <button
            onClick={() => setIsCollapsed((v) => !v)}
            className="rounded-md p-1 text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
            title={isCollapsed ? "Развернуть" : "Свернуть"}
          >
            {isCollapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
          </button>
        </div>

        <div className="mx-3 mb-4 border-t border-white/[0.06]" />

        {!isCollapsed && (
          <div className="mx-3 mb-4 flex items-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.03] px-3 py-2 text-sm text-zinc-500">
            <Search size={15} />
            <span>Поиск тендеров…</span>
          </div>
        )}

        <nav className="flex flex-col gap-0.5 px-3">
          {NAV_ITEMS.filter((item) => !item.adminOnly || user?.role === "admin").map(
            (item) => {
              const Icon = item.icon;
              // Вложенные адреса (`/tenders/:id`) подсвечивают свой раздел; корень «/» —
              // только при точном совпадении, иначе «Дашборд» горел бы везде.
              const isActive =
                item.to === "/"
                  ? location.pathname === "/"
                  : location.pathname === item.to || location.pathname.startsWith(`${item.to}/`);

              if (item.disabled) {
                return (
                  <span
                    key={item.label}
                    className="flex cursor-default items-center gap-3 rounded-xl border border-transparent px-3 py-2.5 text-sm text-zinc-600"
                    title="Появится в следующих этапах разработки"
                  >
                    <Icon size={17} />
                    {!isCollapsed && <span>{item.label}</span>}
                  </span>
                );
              }

              return (
                <Link
                  key={item.label}
                  to={item.to}
                  className={`flex items-center gap-3 rounded-xl border px-3 py-2.5 text-sm font-medium transition-colors ${
                    isActive
                      ? "border-white/10 bg-white/[0.07] text-white shadow-sm"
                      : "border-transparent text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-300"
                  }`}
                  title={item.label}
                >
                  <Icon size={17} />
                  {!isCollapsed && <span>{item.label}</span>}
                </Link>
              );
            },
          )}
        </nav>

        {!isCollapsed && (
          <div className="mt-auto whitespace-nowrap p-4 text-xs text-zinc-700">
            Sova Scanner · Этапы 0–13
          </div>
        )}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="z-30 flex h-16 shrink-0 items-center justify-end border-b border-white/[0.05] bg-zinc-950/80 px-8 backdrop-blur">
          <AccountMenu />
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}
