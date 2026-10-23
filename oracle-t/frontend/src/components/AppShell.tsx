import { ChevronDown, PanelLeftClose, PanelLeftOpen, Search } from "lucide-react";
import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../context/useAuth";
import { useNavLayout } from "../hooks/useNavLayout";
import { SPRING_SNAPPY, SPRING_SOFT, navItemVariants, pageVariants, staggerContainer } from "../utils/motion";
import { AccountMenu } from "./AccountMenu";
import { Logo, LogoMark } from "./Logo";
import { HeaderNav } from "./nav/HeaderNav";
import { NotificationCenter } from "./nav/NotificationCenter";
import { PageHeaderSlotContext } from "./nav/pageHeaderSlotContext";
import { CommandPalette } from "./nav/CommandPalette";
import { SidebarNavLink } from "./nav/SidebarNavLink";
import { WhatsNewPill } from "./nav/WhatsNew";
import { NAV_GROUPS, isNavItemActive, visibleNavItems } from "./nav/navConfig";
import type { NavItem } from "./nav/navConfig";

const COLLAPSED_KEY = "nav.sidebarCollapsed";
// Какие группы боковой панели раскрыты («Тендеры» → «Релевантные», «Неактуальные»).
const EXPANDED_KEY = "nav.sidebarExpanded";

function readExpanded(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(EXPANDED_KEY);
    return raw ? (JSON.parse(raw) as Record<string, boolean>) : {};
  } catch {
    return {};
  }
}

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
  // Узел в шапке, куда страница ставит свой элемент (поиск на «Тендерах»). Через state, а
  // не ref: страница должна перерисоваться, когда узел появится.
  const [headerSlot, setHeaderSlot] = useState<HTMLElement | null>(null);

  // Контейнер прокрутки теперь переживает переходы — новая страница должна начинаться сверху.
  useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 });
  }, [pathname]);

  const main = (
    <main ref={mainRef} className="min-h-0 flex-1 overflow-y-auto">
      <PageHeaderSlotContext.Provider value={headerSlot}>
        <Outlet />
      </PageHeaderSlotContext.Provider>
    </main>
  );
  // Тема переехала в меню аватара (14.10.2026): переключают её раз в жизни, а в шапке она
  // занимала место, которое теперь отдано поиску.
  const slot = <div ref={setHeaderSlot} className="flex min-w-0 max-w-[640px] flex-1 items-center" />;

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
          {slot}
          <div className="ml-auto flex items-center gap-4">
            <NotificationCenter />
            <WhatsNewPill />
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
        <header className="z-30 flex h-16 shrink-0 items-center gap-4 border-b border-white/[0.05] bg-zinc-950/80 px-6 backdrop-blur">
          {slot}
          {/* Учётная запись — карточкой внизу боковой панели (23.10.2026). */}
          <div className="ml-auto flex items-center gap-4">
            <NotificationCenter />
          </div>
        </header>
        {main}
      </div>
    </div>
  );
}

// Мак показывает подсказку ⌘K, остальные — Ctrl K: сочетание одно и то же (metaKey/ctrlKey).
const IS_MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

/**
 * Боковая панель (редизайн 23.10.2026 по образцу соседнего проекта): бренд с подписью,
 * поле быстрого перехода (⌘K), разделы по группам с подписями, активный пункт — на
 * приподнятой плашке, которая перетекает между пунктами. Внизу — «Настройки», ниже
 * карточка учётной записи (меню раскрывается вверх, в нём блок «Что нового?»). Цвета
 * прежние: фон zinc-950, знак — в переливающемся градиенте бренда.
 *
 * Сворачивает панель нажатие на знак (по наведению сова сменяется значком панели) —
 * отдельная кнопка внизу убрана. Высоты пунктов и отступы тянутся по высоте окна
 * (`.sidebar-fit` в index.css), чтобы меню целиком помещалось без прокрутки.
 */
function Sidebar() {
  const { user } = useAuth();
  const { pathname, search } = useLocation();
  const [isCollapsed, setIsCollapsed] = useState(readCollapsed);
  const [isPaletteOpen, setIsPaletteOpen] = useState(false);

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

  // ⌘K / Ctrl+K открывает быстрый переход. В «Настройках» сочетание уже занято поиском по
  // настройкам — там уступаем ему, чтобы не открывалось два поиска сразу.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "k" || !(event.metaKey || event.ctrlKey)) return;
      if (pathname.startsWith("/settings")) return;
      event.preventDefault();
      setIsPaletteOpen((open) => !open);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [pathname]);

  // В боковой панели — основные разделы; «Избранное» живёт только в выпадающем меню шапки
  // (здесь до него один клик из меню учётной записи).
  const allItems = visibleNavItems(user).filter((item) => !item.headerOnly);
  const items = allItems.filter((item) => !item.parent && !item.sidebarFooter);
  const footerItems = allItems.filter((item) => item.sidebarFooter);
  const childrenOf = (item: NavItem) => allItems.filter((child) => child.parent === item.to);
  const groups = NAV_GROUPS.map((group) => ({
    ...group,
    items: items.filter((item) => item.group === group.key),
  })).filter((group) => group.items.length > 0);
  const [expanded, setExpanded] = useState<Record<string, boolean>>(readExpanded);
  const toggleGroup = (to: string) => {
    setExpanded((prev) => {
      const next = { ...prev, [to]: !prev[to] };
      try {
        localStorage.setItem(EXPANDED_KEY, JSON.stringify(next));
      } catch {
        // без хранилища группа просто не запомнит состояние
      }
      return next;
    });
  };

  const renderItem = (item: NavItem) => {
    const children = childrenOf(item);
    const childActive = children.some((child) => isNavItemActive(child, pathname, search));
    // В свёрнутой панели вложенных пунктов не видно — подсвечивается родитель.
    const isActive = isNavItemActive(item, pathname, search) || (isCollapsed && childActive);
    // Открытый вложенный раздел раскрывает группу сам, иначе его не видно в меню.
    const isOpen = !isCollapsed && children.length > 0 && (expanded[item.to] || childActive);
    return (
      <motion.div key={item.to} variants={navItemVariants}>
        <div className="relative flex items-center">
          <SidebarNavLink
            to={item.to}
            label={item.label}
            icon={item.icon}
            isActive={isActive}
            collapsed={isCollapsed}
            // место под стрелку группы, чтобы длинная подпись не уходила под неё
            trailing={children.length > 0 ? <span className="w-6" /> : undefined}
          />
          {!isCollapsed && children.length > 0 && (
            <button
              type="button"
              onClick={() => toggleGroup(item.to)}
              aria-expanded={isOpen}
              title={isOpen ? "Свернуть подразделы" : "Показать подразделы"}
              className="absolute right-2 z-10 flex h-7 w-7 items-center justify-center rounded-full text-zinc-500 transition-colors hover:bg-white/[0.08] hover:text-zinc-100"
            >
              <ChevronDown
                size={15}
                className={`transition-transform duration-300 ease-out ${isOpen ? "rotate-180" : ""}`}
              />
            </button>
          )}
        </div>
        <AnimatePresence initial={false}>
          {isOpen && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={SPRING_SNAPPY}
              className="overflow-hidden"
            >
              <div className="my-0.5 ml-[22px] flex flex-col gap-0.5 border-l border-white/[0.08] pl-3">
                {children.map((child) => (
                  <SidebarNavLink
                    key={child.to}
                    to={child.to}
                    label={child.label}
                    icon={child.icon}
                    isActive={isNavItemActive(child, pathname, search)}
                    collapsed={false}
                    nested
                  />
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    );
  };

  return (
    <motion.aside
      initial={false}
      animate={{ width: isCollapsed ? 76 : 272 }}
      transition={SPRING_SOFT}
      className="sidebar-fit flex h-screen shrink-0 flex-col overflow-hidden border-r border-white/[0.06] bg-zinc-950"
    >
      <div className="shrink-0 px-4 pt-[var(--nav-gap)]">
        <button
          type="button"
          onClick={toggle}
          title={isCollapsed ? "Развернуть меню" : "Свернуть меню"}
          aria-label={isCollapsed ? "Развернуть меню" : "Свернуть меню"}
          className="group flex w-full items-center gap-3 text-left"
        >
          <span className="brand-gradient relative flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-[14px] text-snow transition-transform duration-200 group-hover:scale-[1.04] group-active:scale-95">
            <LogoMark
              size={15}
              className="text-snow drop-shadow transition-[opacity,transform] duration-200 group-hover:scale-75 group-hover:opacity-0"
            />
            {isCollapsed ? (
              <PanelLeftOpen
                size={18}
                className="absolute scale-75 opacity-0 transition-[opacity,transform] duration-200 group-hover:scale-100 group-hover:opacity-100"
              />
            ) : (
              <PanelLeftClose
                size={18}
                className="absolute scale-75 opacity-0 transition-[opacity,transform] duration-200 group-hover:scale-100 group-hover:opacity-100"
              />
            )}
          </span>
          {!isCollapsed && (
            <motion.span
              initial={{ opacity: 0, x: -6 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.25, delay: 0.08 }}
              className="min-w-0 whitespace-nowrap"
            >
              <span className="brand-wordmark block text-[16px] leading-tight text-white">Sova</span>
              <span className="block text-xs text-zinc-500">Тендерная аналитика</span>
            </motion.span>
          )}
        </button>
        <div className="my-[var(--nav-gap)] h-px bg-white/[0.06]" />
        <button
          type="button"
          onClick={() => setIsPaletteOpen(true)}
          title={`Поиск (${IS_MAC ? "⌘K" : "Ctrl K"})`}
          className="nav-well group flex h-[var(--nav-item)] w-full items-center gap-3 whitespace-nowrap rounded-2xl px-[13px] text-sm text-zinc-500 transition-[color,transform] duration-200 hover:text-zinc-300 active:scale-[0.98]"
        >
          <Search size={18} className="shrink-0 transition-colors group-hover:text-zinc-200" />
          {!isCollapsed && (
            <>
              <span className="flex-1 text-left">Поиск…</span>
              <kbd className="rounded-full border border-white/[0.06] bg-white/[0.06] px-2 py-0.5 font-sans text-[11px] font-medium text-zinc-400">
                {IS_MAC ? "⌘K" : "Ctrl K"}
              </kbd>
            </>
          )}
        </button>
      </div>

      <LayoutGroup id="sidebar-nav">
        <motion.nav
          variants={staggerContainer}
          initial="initial"
          animate="animate"
          className="no-scrollbar flex min-h-0 flex-1 flex-col overflow-y-auto overflow-x-hidden px-4 pb-[var(--nav-gap)]"
        >
          {groups.map((group, index) => (
            <div key={group.key} className="flex flex-col gap-0.5">
              {isCollapsed ? (
                <div className={`mx-auto my-[var(--nav-gap)] h-px w-6 bg-white/[0.08] ${index === 0 ? "opacity-0" : ""}`} />
              ) : (
                <motion.div
                  variants={navItemVariants}
                  className="whitespace-nowrap px-[13px] pb-1 pt-[var(--nav-gap)] text-xs font-medium text-zinc-500"
                >
                  {group.label}
                </motion.div>
              )}
              {group.items.map(renderItem)}
            </div>
          ))}
        </motion.nav>

        <div className="shrink-0 px-4 pb-[var(--nav-gap)]">
          <div className="h-px bg-white/[0.06]" />
          <div className="flex flex-col gap-0.5 py-[calc(var(--nav-gap)/1.5)]">
            {footerItems.map((item) => (
              <SidebarNavLink
                key={item.to}
                to={item.to}
                label={item.label}
                icon={item.icon}
                isActive={isNavItemActive(item, pathname, search)}
                collapsed={isCollapsed}
              />
            ))}
          </div>
          <div className="mb-[calc(var(--nav-gap)/1.5)] h-px bg-white/[0.06]" />
          <div className="flex items-center justify-center">
            <AccountMenu variant="sidebar" collapsed={isCollapsed} />
          </div>
        </div>
      </LayoutGroup>

      <CommandPalette open={isPaletteOpen} onClose={() => setIsPaletteOpen(false)} />
    </motion.aside>
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
