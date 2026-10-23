import type { LucideIcon } from "lucide-react";
import { motion } from "motion/react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { SPRING_SNAPPY } from "../../utils/motion";

// `w-full`, а не `flex-1`: в нижнем блоке пункты стоят в колонке, и `flex-1` (основа 0)
// сжимал их по высоте до строки текста — «Что нового?» и «Настройки» наезжали друг на друга.

/**
 * Пункт боковой панели (редизайн 23.10.2026). Активный пункт лежит на «приподнятой»
 * плашке `.nav-pill` (index.css), которая общим `layoutId` перетекает от пункта к пункту —
 * и между списком разделов, и нижним блоком («Настройки»). `trailing` — то, что стоит
 * справа от подписи (место под стрелку группы).
 */
export function SidebarNavLink({
  to,
  label,
  icon: Icon,
  isActive,
  collapsed,
  nested = false,
  trailing,
}: {
  to: string;
  label: string;
  icon: LucideIcon;
  isActive: boolean;
  collapsed: boolean;
  nested?: boolean;
  trailing?: ReactNode;
}) {
  return (
    <Link
      to={to}
      title={label}
      className={`group/link relative flex w-full min-w-0 items-center gap-3 whitespace-nowrap font-medium transition-[color,background-color,transform] duration-200 active:scale-[0.98] ${
        nested ? "h-[var(--nav-sub)] rounded-xl px-2.5 text-[13px]" : "h-[var(--nav-item)] rounded-2xl px-[13px] text-sm"
      } ${isActive ? "text-white" : "text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-100"}`}
    >
      {isActive && (
        <motion.span
          layoutId="sidebar-active"
          className={`nav-pill absolute inset-0 ${nested ? "rounded-xl" : "rounded-2xl"}`}
          transition={SPRING_SNAPPY}
        />
      )}
      <Icon
        size={nested ? 15 : 18}
        className={`relative shrink-0 transition-[color,transform] duration-200 ${
          isActive ? "text-white" : "text-zinc-500 group-hover/link:scale-105 group-hover/link:text-zinc-200"
        }`}
      />
      {!collapsed && <span className="relative min-w-0 flex-1 truncate">{label}</span>}
      {!collapsed && trailing && <span className="relative flex shrink-0 items-center">{trailing}</span>}
    </Link>
  );
}
