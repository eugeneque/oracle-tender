import { Check, PanelLeft, PanelTop } from "lucide-react";
import { motion } from "motion/react";

import { useNavLayout } from "../../hooks/useNavLayout";
import type { NavLayout } from "../../hooks/useNavLayout";
import { SPRING_SNAPPY } from "../../utils/motion";
import { SettingsGroup } from "./ui";

// Вид главного меню (28.09.2026): боковая панель или шапка. Карточки выбора — с мини-схемой
// экрана, чтобы разница читалась без слов: серым — меню, светлым — место под карточку тендера.

const OPTIONS: { value: NavLayout; title: string; hint: string; icon: typeof PanelLeft }[] = [
  {
    value: "sidebar",
    title: "Боковое меню",
    hint: "Разделы слева, как раньше. Панель можно свернуть до иконок.",
    icon: PanelLeft,
  },
  {
    value: "header",
    title: "Меню в шапке",
    hint: "Разделы наверху с выпадающими подпунктами — вся ширина экрана отдаётся тендерам.",
    icon: PanelTop,
  },
];

function Preview({ layout }: { layout: NavLayout }) {
  const bar = "rounded-sm bg-white/15";
  const card = "rounded-sm bg-white/[0.06] border border-white/[0.06]";
  if (layout === "sidebar") {
    return (
      <div className="flex h-24 gap-1.5 rounded-lg border border-white/[0.08] bg-zinc-950 p-1.5">
        <div className="flex w-10 shrink-0 flex-col gap-1 rounded-sm bg-white/[0.04] p-1">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className={`h-1.5 ${bar} ${i === 0 ? "bg-indigo-400/60" : ""}`} />
          ))}
          <div className="mt-auto h-4 rounded-sm bg-indigo-500/30" />
        </div>
        <div className="flex flex-1 gap-1">
          <div className={`w-2/5 ${card}`} />
          <div className={`flex-1 ${card}`} />
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-24 flex-col gap-1.5 rounded-lg border border-white/[0.08] bg-zinc-950 p-1.5">
      <div className="flex h-3.5 shrink-0 items-center gap-1 rounded-sm bg-white/[0.04] px-1">
        <div className="h-1.5 w-3 rounded-sm bg-white/25" />
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className={`h-1 w-4 ${bar} ${i === 0 ? "bg-indigo-400/60" : ""}`} />
        ))}
        <div className="ml-auto h-1.5 w-6 rounded-full bg-gradient-to-r from-indigo-400/70 to-fuchsia-400/70" />
      </div>
      <div className="flex flex-1 gap-1">
        <div className={`w-2/5 ${card}`} />
        <div className={`flex-1 ${card}`} />
      </div>
    </div>
  );
}

export function NavLayoutSection() {
  const { layout, setLayout } = useNavLayout();

  return (
    <SettingsGroup
      id="interface-nav"
      label="Вид меню"
    >
      <div role="radiogroup" aria-label="Вид меню" className="grid gap-3 sm:grid-cols-2">
        {OPTIONS.map((option) => {
          const isActive = layout === option.value;
          const Icon = option.icon;
          return (
            <motion.button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={isActive}
              onClick={() => setLayout(option.value)}
              whileHover={{ y: -2 }}
              whileTap={{ scale: 0.98 }}
              transition={SPRING_SNAPPY}
              className={`relative rounded-2xl border p-4 text-left transition-colors ${
                isActive ? "border-transparent" : "border-white/[0.08] hover:border-white/15"
              }`}
            >
              {isActive && (
                <motion.span
                  layoutId="nav-layout-selected"
                  className="absolute inset-0 rounded-2xl border border-indigo-400/60 bg-indigo-500/[0.07] shadow-[0_0_0_3px_rgb(var(--c-indigo-500)_/_0.12)]"
                  transition={SPRING_SNAPPY}
                />
              )}
              <div className="relative">
                <Preview layout={option.value} />
                <div className="mt-3 flex items-center gap-2">
                  <Icon size={15} className={isActive ? "text-indigo-300" : "text-zinc-500"} />
                  <span className="text-sm font-medium text-zinc-100">{option.title}</span>
                  {isActive && (
                    <motion.span
                      initial={{ scale: 0 }}
                      animate={{ scale: 1 }}
                      transition={SPRING_SNAPPY}
                      className="ml-auto flex h-5 w-5 items-center justify-center rounded-full bg-indigo-500 text-snow"
                    >
                      <Check size={12} strokeWidth={3} />
                    </motion.span>
                  )}
                </div>
                <p className="mt-1 text-xs leading-relaxed text-zinc-500">{option.hint}</p>
              </div>
            </motion.button>
          );
        })}
      </div>
    </SettingsGroup>
  );
}
