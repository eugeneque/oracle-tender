import { ArrowRight, CornerDownLeft, Search, Sparkles } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import type { KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../../context/useAuth";
import { dialogVariants, scrimVariants } from "../../utils/motion";
import { visibleNavItems } from "./navConfig";
import { WHATS_NEW_PATH } from "./WhatsNew";

interface Entry {
  key: string;
  label: string;
  hint: string;
  to: string;
  icon: LucideIcon;
}

/**
 * Быстрый переход (⌘K) — поле «Поиск…» вверху боковой панели (23.10.2026). Ищет по
 * разделам и подсказкам к ним, а набранный текст первой строкой предлагает искать в
 * закупках: `/tenders?search=…` подставляет его в поиск списка тендеров.
 */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setCursor(0);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    // Esc — на уровне окна: фокус мог уйти из поля (щелчок по списку), а закрыться надо всё равно.
    const onEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [open, onClose]);

  const entries = useMemo<Entry[]>(() => {
    const q = query.trim().toLowerCase();
    const sections: Entry[] = [
      ...visibleNavItems(user).map((item) => ({
        key: item.to,
        label: item.label,
        hint: item.hint,
        to: item.to,
        icon: item.icon,
      })),
      { key: "whats-new", label: "Что нового?", hint: "Последние нововведения", to: `${WHATS_NEW_PATH}?tab=news`, icon: Sparkles },
    ].filter((entry) => !q || `${entry.label} ${entry.hint}`.toLowerCase().includes(q));
    if (!q) return sections;
    return [
      {
        key: "search",
        label: `Искать «${query.trim()}» в закупках`,
        hint: "По названию и заказчику",
        to: `/tenders?search=${encodeURIComponent(query.trim())}`,
        icon: Search,
      },
      ...sections,
    ];
  }, [query, user]);

  const go = (entry: Entry | undefined) => {
    if (!entry) return;
    onClose();
    navigate(entry.to);
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((c) => Math.min(c + 1, entries.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(entries[cursor]);
    }
  };

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div key="scrim" variants={scrimVariants} initial="initial" animate="animate" exit="exit">
          <div className="fixed inset-0 z-40 bg-scrim/50 backdrop-blur-sm" onClick={onClose} />
        </motion.div>
      )}
      {open && (
        <motion.div
          key="palette"
          variants={dialogVariants}
          initial="initial"
          animate="animate"
          exit="exit"
          className="fixed inset-x-0 top-[14vh] z-50 mx-auto w-[min(560px,calc(100vw-32px))]"
        >
          <div className="nav-popover relative overflow-hidden rounded-[22px]">
            <div className="flex items-center gap-3 border-b border-white/[0.07] px-4">
              <Search size={16} className="shrink-0 text-zinc-500" />
              <input
                autoFocus
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setCursor(0);
                }}
                onKeyDown={onKeyDown}
                placeholder="Раздел или закупка…"
                className="h-14 flex-1 bg-transparent text-[15px] text-white placeholder-zinc-500 outline-none"
              />
              <kbd className="rounded-md border border-white/[0.08] bg-white/[0.05] px-1.5 py-0.5 text-[11px] text-zinc-400">
                Esc
              </kbd>
            </div>
            <ul className="max-h-[50vh] overflow-y-auto p-1.5">
              {entries.length === 0 && (
                <li className="px-3 py-6 text-center text-sm text-zinc-500">Ничего не нашлось</li>
              )}
              {entries.map((entry, i) => {
                const Icon = entry.icon;
                const active = i === cursor;
                return (
                  <li key={entry.key}>
                    <button
                      onMouseMove={() => setCursor(i)}
                      onClick={() => go(entry)}
                      className={`relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left ${
                        active ? "text-white" : "text-zinc-300"
                      }`}
                    >
                      {active && (
                        <motion.span
                          layoutId="palette-cursor"
                          className="nav-pill absolute inset-0 rounded-xl"
                          transition={{ type: "spring", bounce: 0, duration: 0.25 }}
                        />
                      )}
                      <Icon size={16} className={`relative shrink-0 ${active ? "text-white" : "text-zinc-500"}`} />
                      <span className="relative min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{entry.label}</span>
                        <span className="block truncate text-xs text-zinc-500">{entry.hint}</span>
                      </span>
                      {active &&
                        (entry.key === "search" ? (
                          <CornerDownLeft size={14} className="relative shrink-0 text-zinc-400" />
                        ) : (
                          <ArrowRight size={14} className="relative shrink-0 text-zinc-400" />
                        ))}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
