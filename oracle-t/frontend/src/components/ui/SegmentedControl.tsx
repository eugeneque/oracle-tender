import { motion } from "motion/react";
import { useId } from "react";
import type { KeyboardEvent, ReactNode } from "react";

import { SPRING_SNAPPY } from "../../utils/motion";

// Переключатель из нескольких сегментов с «переезжающей» подложкой — порт AnimatedTabs из
// smoothui (вариант pill/segment) под палитру ORACLE-T. Стрелки ←/→ переключают сегменты.

export interface Segment<T extends string> {
  value: T;
  label: ReactNode;
  icon?: ReactNode;
  title?: string;
}

export function SegmentedControl<T extends string>({
  value,
  onChange,
  segments,
  size = "md",
  shape = "rounded",
  ariaLabel,
}: {
  value: T;
  onChange: (value: T) => void;
  segments: Segment<T>[];
  size?: "sm" | "md";
  shape?: "rounded" | "pill";
  ariaLabel?: string;
}) {
  const layoutId = `segment-${useId()}`;
  const radius = shape === "pill" ? "rounded-full" : size === "sm" ? "rounded-md" : "rounded-lg";

  const onKeyDown = (e: KeyboardEvent, index: number) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = segments[(index + step + segments.length) % segments.length];
    onChange(next.value);
    document.getElementById(`${layoutId}-${next.value}`)?.focus();
  };

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={`flex items-center gap-0.5 border border-white/[0.08] bg-white/[0.03] ${
        shape === "pill" ? "rounded-full p-1" : size === "sm" ? "rounded-lg p-0.5" : "rounded-xl p-1"
      }`}
    >
      {segments.map((segment, index) => {
        const isActive = segment.value === value;
        return (
          <button
            key={segment.value}
            id={`${layoutId}-${segment.value}`}
            type="button"
            role="tab"
            aria-selected={isActive}
            tabIndex={isActive ? 0 : -1}
            title={segment.title}
            onClick={() => onChange(segment.value)}
            onKeyDown={(e) => onKeyDown(e, index)}
            className={`relative flex items-center gap-1.5 font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-indigo-500/60 ${radius} ${
              size === "sm" ? "px-2.5 py-1 text-xs" : "px-4 py-2 text-sm"
            } ${isActive ? "text-white" : "text-zinc-500 hover:text-zinc-300"}`}
          >
            {isActive && (
              <motion.span
                layoutId={layoutId}
                className={`absolute inset-0 bg-white/10 shadow-sm ${radius}`}
                transition={SPRING_SNAPPY}
              />
            )}
            {segment.icon && <span className="relative flex">{segment.icon}</span>}
            <span className="relative">{segment.label}</span>
          </button>
        );
      })}
    </div>
  );
}
