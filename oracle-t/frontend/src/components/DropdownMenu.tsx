import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

export interface DropdownItem {
  key: string;
  label: ReactNode;
  icon?: ReactNode;
  hint?: string;
  active?: boolean;
  disabled?: boolean;
  onSelect: () => void;
}

/**
 * Меню «Ещё» — для действий, которые нужны не на каждом заходе.
 *
 * Страница тендеров обрастала кнопками быстрее, чем у неё было места: каждая редкая
 * операция (выгрузка, выбор площадок, ручная заявка) стоила постоянной кнопки на виду и
 * отнимала внимание у самой закупки. Здесь они остаются в одном клике, но не мешают.
 */
export function DropdownMenu({
  trigger,
  items,
  align = "right",
  title,
  buttonClassName,
}: {
  trigger: ReactNode;
  items: DropdownItem[];
  align?: "left" | "right";
  title?: string;
  buttonClassName?: string;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setIsOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [isOpen]);

  return (
    <div ref={rootRef} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setIsOpen((v) => !v)}
        title={title}
        aria-haspopup="menu"
        aria-expanded={isOpen}
        className={
          buttonClassName ??
          `flex h-9 items-center gap-1.5 rounded-lg px-2.5 text-sm transition-colors ${
            isOpen ? "bg-white/10 text-white" : "text-zinc-400 hover:bg-white/5 hover:text-zinc-200"
          }`
        }
      >
        {trigger}
      </button>
      {isOpen && (
        <div
          role="menu"
          className={`absolute top-full z-40 mt-1 min-w-[220px] overflow-hidden rounded-xl border border-white/10 bg-zinc-900 py-1 shadow-[0_20px_60px_rgba(0,0,0,0.35)] ${
            align === "right" ? "right-0" : "left-0"
          }`}
        >
          {items.map((item) => (
            <button
              key={item.key}
              role="menuitem"
              type="button"
              disabled={item.disabled}
              title={item.hint}
              onClick={() => {
                setIsOpen(false);
                item.onSelect();
              }}
              className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition-colors disabled:opacity-40 ${
                item.active ? "bg-white/[0.06] text-white" : "text-zinc-300 hover:bg-white/5 hover:text-white"
              }`}
            >
              {item.icon && <span className="flex w-4 shrink-0 justify-center text-zinc-500">{item.icon}</span>}
              <span className="min-w-0 flex-1">{item.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
