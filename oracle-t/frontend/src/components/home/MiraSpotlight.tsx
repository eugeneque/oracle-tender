import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useState } from "react";
import { ArrowRight, Plus } from "lucide-react";
import { Link } from "react-router-dom";

import { Mira } from "../mira/Mira";
import type { MiraMood } from "../mira/Mira";
import { MiraFrame } from "../mira/MiraFrame";
import { SlideDetails } from "./BentoRecap";
import type { Slide } from "../../content/homeSlides";

/**
 * Большой блок «Знакомьтесь: Мира» над мозаикой «Что нового?» (24.10.2026). Мира здесь не
 * картинка, а настоящий маскот из карточки закупки: она проигрывает разбор по кругу — ждёт,
 * читает документы, сверяет с каталогом, думает и радуется итогу. Рамка блока — та же
 * `MiraFrame`: во время «работы» по контуру бежит комета и заполняются шаги «N из 3», так
 * что блок показывает ровно то, что пользователь увидит в закупке. За Мирой — мягкое
 * свечение в цвет её настроения (`.mira-spotlight__glow` в index.css).
 */

interface Stage {
  mood: MiraMood;
  busy: boolean;
  /** Ход задачи в формате сервера — для шкалы по рамке. */
  progress: string | null;
  caption: string;
  ms: number;
}

const STAGES: Stage[] = [
  { mood: "idle", busy: false, progress: null, caption: "Ждёт закупку — и следит за курсором", ms: 3600 },
  { mood: "reading", busy: true, progress: "Шаг 1 из 3", caption: "Читает документацию", ms: 3200 },
  { mood: "comparing", busy: true, progress: "Шаг 2 из 3", caption: "Сверяет требования с каталогом", ms: 3200 },
  { mood: "thinking", busy: true, progress: "Шаг 3 из 3", caption: "Думает над заключением", ms: 3200 },
  { mood: "happy", busy: false, progress: null, caption: "Готово: подходим", ms: 3600 },
];

const REDUCED =
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function MiraSpotlight({ slide }: { slide: Slide }) {
  const [index, setIndex] = useState(0);
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const stage = STAGES[index];

  useEffect(() => {
    if (REDUCED) return;
    const timer = setTimeout(() => setIndex((n) => (n + 1) % STAGES.length), stage.ms);
    return () => clearTimeout(timer);
  }, [index, stage.ms]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", bounce: 0.2, duration: 0.8 }}
      className="mb-3"
    >
      <MiraFrame
        tone={stage.busy ? "working" : stage.mood}
        busy={stage.busy}
        progress={stage.progress}
        radius={28}
        className="mira-spotlight surface overflow-hidden rounded-[28px]"
      >
        <span className="mira-spotlight__glow pointer-events-none absolute inset-0" data-tone={stage.busy ? "working" : stage.mood} />
        <div className="relative z-[1] grid items-center gap-8 px-8 py-10 md:grid-cols-[1.15fr_1fr] md:px-12 md:py-12">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium text-zinc-400">
              <span className="rounded-full bg-indigo-500 px-2 py-0.5 text-[10.5px] font-semibold uppercase tracking-wider text-snow">
                Новое
              </span>
              {slide.kicker}
            </div>
            <h2 className="mt-4 text-4xl font-semibold tracking-tight text-zinc-50 md:text-5xl">
              Знакомьтесь: <span className="mira-spotlight__name">Мира</span>
            </h2>
            <p className="mt-4 max-w-md text-[15px] leading-relaxed text-zinc-400">
              {slide.lead} Она читает документацию, сверяет требования с каталогом и по настроению сразу показывает итог.
            </p>
            <div className="mt-7 flex flex-wrap items-center gap-2">
              <button
                onClick={() => setOpen(true)}
                className="inline-flex items-center gap-2 rounded-full bg-indigo-500 px-4 py-2 text-sm font-medium text-snow transition-colors hover:bg-indigo-400"
              >
                <Plus size={15} /> Подробнее
              </button>
              {slide.link && (
                <Link
                  to={slide.link.to}
                  className="inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium text-zinc-400 transition-colors hover:bg-white/[0.06] hover:text-zinc-100"
                >
                  {slide.link.label} <ArrowRight size={15} />
                </Link>
              )}
            </div>
          </div>

          <div className="flex flex-col items-center">
            <div className="relative flex h-[200px] w-[200px] items-center justify-center md:h-[230px] md:w-[230px]">
              <span className="mira-spotlight__halo absolute inset-6 rounded-full" />
              <Mira mood={stage.mood} busy={stage.busy} size={170} />
            </div>
            <div className="mt-4 h-6 text-center">
              <AnimatePresence mode="wait">
                <motion.span
                  key={stage.caption}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{ duration: 0.25 }}
                  className="block text-sm font-medium text-zinc-200"
                >
                  {stage.caption}
                </motion.span>
              </AnimatePresence>
            </div>
            <div className="mt-3 flex gap-1.5" role="tablist" aria-label="Состояния Миры">
              {STAGES.map((item, n) => (
                <button
                  key={item.caption}
                  role="tab"
                  aria-selected={n === index}
                  aria-label={item.caption}
                  onClick={() => setIndex(n)}
                  className={`h-1.5 rounded-full transition-all duration-300 ${
                    n === index ? "w-6 bg-zinc-200" : "w-1.5 bg-zinc-700 hover:bg-zinc-500"
                  }`}
                />
              ))}
            </div>
          </div>
        </div>
      </MiraFrame>
      {open && <SlideDetails slide={slide} onClose={close} />}
    </motion.div>
  );
}
