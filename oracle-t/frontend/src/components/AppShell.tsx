import { ChevronsLeft, ChevronsRight } from "lucide-react";
import { LayoutGroup, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../context/useAuth";
import { useNavLayout } from "../hooks/useNavLayout";
import { SPRING_SNAPPY, SPRING_SOFT, navItemVariants, pageVariants, staggerContainer } from "../utils/motion";
import { AccountMenu } from "./AccountMenu";
import { Logo, LogoMark } from "./Logo";
import { ThemeToggle } from "./ThemeToggle";
import { HeaderNav } from "./nav/HeaderNav";
import { MyJobsIndicator } from "./nav/MyJobsIndicator";
import { WhatsNewCard, WhatsNewPill } from "./nav/WhatsNew";
import { isNavItemActive, visibleNavItems } from "./nav/navConfig";

const COLLAPSED_KEY = "nav.sidebarCollapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * Постоянный каркас приложения — layout-маршрут в App.tsx (28.09.2026). Раньше каждая
 * страница оборачивала себя в оболочку сама, и меню пересоздавалось при каждом переходе:
 * сбрасывалось свёрнутое состояние и не могли работать анимации пунктов. Вид меню —
 * боковая панель или шапка — выбирается в настройках (useNavLayout).
 *
 * `h-screen`, а не `min-h-screen`: страницам нужен предсказуемый потолок высоты, чтобы
 * внутри них скроллились отдельные панели, а не весь документ — иначе в двухпанельном
 * режиме приходится сначала пролистать страницу и только потом карточку. Страницы, которым
 * нужен обычный скролл, прокручивают общий контейнер `<main>`.
 */
export function AppLayout() {
  const { layout } = useNavLayout();
  const { pathname } = useLocation();
  const mainRef = useRef<HTMLElement>(null);

  // Контейнер прокрутки теперь переживает переходы — новая страница должна начинаться сверху.
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 });
  }, [pathname]);

  const main = (
    <main ref={mainRef} className="min-h-0 flex-1 overflow-y-auto">
      <Outlet />
    </main>
  );

  if (layout === "header") {
    return (
      <div className="flex h-screen flex-col bg-zinc-950">
        <header className="relative z-30 flex h-16 shrink-0 items-center gap-6 border-b border-white/[0.06] bg-zinc-950/80 px-6 backdrop-blur">
          <Link to="/tenders" className="shrink-0" title="Тендеры">
            <Logo size={24} />
          </Link>
          <LayoutGroup id="header-nav">
            <HeaderNav />
          </LayoutGroup>
          <div className="ml-auto flex items-center gap-4">
            <MyJobsIndicator />
            <WhatsNewPill />
            <ThemeToggle />
            <AccountMenu />
          </div>
        </header>
        {main}
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-zinc-950">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="z-30 flex h-16 shrink-0 items-center justify-end gap-4 border-b border-white/[0.05] bg-zinc-950/80 px-8 backdrop-blur">
          <MyJobsIndicator />
          <ThemeToggle />
          <AccountMenu />
        </header>
        {main}
      </div>
    </div>
  );
}

function Sidebar() {
  const { user } = useAuth();
  const { pathname, search } = useLocation();
  const [isCollapsed, setIsCollapsed] = useState(readCollapsed);

  const toggle = () => {
    setIsCollapsed((prev) => {
      try {
        localStorage.setItem(COLLAPSED_KEY, prev ? "0" : "1");
      } catch {
        // без хранилища панель просто не запомнит состояние
      }
      return !prev;
    });
  };

  // В боковой панели — основные разделы; «Избранное» живёт только в выпадающем меню шапки
  // (здесь до него один клик из меню учётной записи).
  const items = visibleNavItems(user).filter((item) => !item.headerOnly);

  return (
    <motion.aside
      initial={false}
      animate={{ width: isCollapsed ? 76 : 260 }}
      transition={SPRING_SOFT}
      className="flex h-screen shrink-0 flex-col overflow-hidden border-r border-white/[0.06] bg-zinc-950"
    >
      <div className="mb-4 flex h-16 shrink-0">
        <SidebarBrand collapsed={isCollapsed} onToggle={toggle} />
      </div>

      <LayoutGroup id="sidebar-nav">
        <motion.nav
          variants={staggerContainer}
          initial="initial"
          animate="animate"
          className="flex flex-col gap-0.5 px-3"
        >
          {items.map((item) => {
            const Icon = item.icon;
            const isActive = isNavItemActive(item, pathname, search);
            return (
              <motion.div key={item.to} variants={navItemVariants}>
                <Link
                  to={item.to}
                  className={`relative flex items-center gap-3 whitespace-nowrap rounded-xl px-3 py-2.5 text-sm font-medium transition-colors ${
                    isActive ? "text-white" : "text-zinc-500 hover:bg-white/[0.04] hover:text-zinc-300"
                  }`}
                  title={item.label}
                >
                  {isActive && (
                    <motion.span
                      layoutId="sidebar-active"
                      className="absolute inset-0 rounded-xl border border-white/10 bg-white/[0.07] shadow-sm"
                      transition={SPRING_SNAPPY}
                    />
                  )}
                  <Icon size={17} className="relative shrink-0" />
                  {!isCollapsed && <span className="relative">{item.label}</span>}
                </Link>
              </motion.div>
            );
          })}
        </motion.nav>
      </LayoutGroup>

      <div className={`mt-auto ${isCollapsed ? "pb-4" : "p-3"}`}>
        <WhatsNewCard collapsed={isCollapsed} />
        {!isCollapsed && (
          <div className="mt-3 whitespace-nowrap px-1 text-xs text-zinc-700">Sova</div>
        )}
      </div>
    </motion.aside>
  );
}

/**
 * Блок бренда вверху боковой панели: переливающийся градиент (.brand-gradient в index.css),
 * на нём сова и «SOVA». Блок без скруглений заливает всю шапку панели — от её правой границы
 * до линии шапки страницы, — а ширина следует за панелью: при сворачивании остаётся квадрат
 * с одной совой. Отступ слева постоянный (сова по центру свёрнутой панели), знак не прыгает. Стрелка сворачивания — внутри
 * блока справа; в свёрнутом виде разворачивает весь квадрат (по наведению сова → стрелка).
 */
function SidebarBrand({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const box = "brand-gradient brand-gradient--flush group relative flex h-full min-w-0 flex-1 items-center overflow-hidden text-snow";
  const mark = (
    <span className="relative flex h-6 w-6 shrink-0 items-center justify-center">
      <LogoMark
        size={18}
        className={`text-snow drop-shadow transition-opacity duration-200 ${collapsed ? "group-hover:opacity-0" : ""}`}
      />
      {collapsed && (
        <ChevronsRight
          size={18}
          className="absolute opacity-0 transition-opacity duration-200 group-hover:opacity-100"
        />
      )}
    </span>
  );

  if (collapsed) {
    return (
      <button onClick={onToggle} className={`${box} pl-[26px]`} title="Развернуть" aria-label="Развернуть меню">
        {mark}
      </button>
    );
  }
  return (
    <div className={box}>
      <Link to="/tenders" className="flex h-full min-w-0 flex-1 items-center gap-3 pl-[26px]" title="Тендеры">
        {mark}
        <motion.span
          initial={{ opacity: 0, x: -6 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.25, delay: 0.08 }}
          className="brand-wordmark whitespace-nowrap text-[17px] leading-none"
        >
          Sova
        </motion.span>
      </Link>
      <button
        onClick={onToggle}
        className="mr-4 shrink-0 rounded-md p-1 text-snow/70 transition-colors hover:bg-snow/15 hover:text-snow"
        title="Свернуть"
      >
        <ChevronsLeft size={16} />
      </button>
    </div>
  );
}

/**
 * Обёртка содержимого страницы: плавное появление при переходе. Каркас с меню — в
 * `AppLayout`; имя `AppShell` оставлено, чтобы не переписывать импорты страниц.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <motion.div variants={pageVariants} initial="initial" animate="animate" className="h-full">
      {children}
    </motion.div>
  );
}
