import { motion } from "motion/react";
import { useState } from "react";
import { ArrowDown, ChevronDown, Wrench } from "lucide-react";

import { staggerContainer, staggerItem } from "../../utils/motion";
import type { Fix, SlideTone } from "../../content/homeSlides";

// Блок «Исправлено» под мозаикой нововведений (08.10.2026): плитки рассказывают о новом, а
// об исправленных ошибках пользователи узнавали только из журнала изменений. Карточка —
// «было → стало», чтобы тот, кто натыкался на ошибку, узнал её с первой строки. Сначала
// видны свежие, остальные — по кнопке.

const VISIBLE = 6;

const TONE_CHIP: Record<SlideTone, string> = {
  indigo: "bg-indigo-500/15 text-indigo-300",
  sky: "bg-sky-500/15 text-sky-300",
  emerald: "bg-emerald-500/15 text-emerald-300",
  amber: "bg-amber-500/15 text-amber-300",
  violet: "bg-violet-500/15 text-violet-300",
  rose: "bg-rose-500/15 text-rose-300",
};

function FixCard({ fix }: { fix: Fix }) {
  const Icon = fix.icon;
  return (
    <motion.article
      variants={staggerItem}
      className="flex flex-col rounded-[24px] border border-white/[0.07] bg-zinc-900/70 p-5 transition-colors duration-300 hover:border-white/15"
    >
      <div className="flex items-center gap-3">
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${TONE_CHIP[fix.tone]}`}>
          <Icon size={18} />
        </span>
        <div className="min-w-0 text-xs text-zinc-500">
          <span className="text-zinc-400">{fix.area}</span> · {fix.date}
        </div>
      </div>
      <h3 className="mt-3 text-[15px] font-semibold leading-snug tracking-tight text-zinc-100">{fix.title}</h3>
      <div className="mt-3 flex flex-1 flex-col gap-1.5 text-[13px] leading-relaxed">
        <div className="rounded-xl bg-rose-500/[0.06] px-3 py-2 text-zinc-400">
          <span className="mr-1.5 text-[11px] font-medium uppercase tracking-wider text-rose-300/80">Было</span>
          {fix.before}
        </div>
        <ArrowDown size={13} className="mx-auto shrink-0 text-zinc-600" />
        <div className="rounded-xl bg-emerald-500/[0.07] px-3 py-2 text-zinc-200">
          <span className="mr-1.5 text-[11px] font-medium uppercase tracking-wider text-emerald-300">Стало</span>
          {fix.after}
        </div>
      </div>
    </motion.article>
  );
}

export function FixesList({ fixes }: { fixes: Fix[] }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? fixes : fixes.slice(0, VISIBLE);

  return (
    <section className="mt-14" aria-labelledby="fixes-heading">
      <div className="mb-5">
        <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-emerald-300">
          <Wrench size={14} /> Исправлено
        </div>
        <h2 id="fixes-heading" className="mt-1.5 text-3xl font-semibold tracking-tight text-zinc-50">
          Ошибки, которых больше нет
        </h2>
      </div>
      <motion.div
        variants={staggerContainer}
        initial="initial"
        animate="animate"
        className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
      >
        {shown.map((fix) => (
          <FixCard key={fix.id} fix={fix} />
        ))}
      </motion.div>
      {fixes.length > VISIBLE && (
        <div className="mt-4 flex justify-center">
          <button
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            className="inline-flex items-center gap-1.5 rounded-full bg-white/[0.06] px-4 py-2 text-sm text-zinc-300 hover:bg-white/10 hover:text-zinc-100"
          >
            {expanded ? "Свернуть" : `Показать все ${fixes.length}`}
            <ChevronDown size={15} className={`transition-transform ${expanded ? "rotate-180" : ""}`} />
          </button>
        </div>
      )}
    </section>
  );
}
