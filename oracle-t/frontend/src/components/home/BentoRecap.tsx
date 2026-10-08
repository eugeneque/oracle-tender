import { motion } from "motion/react";
import type { Variants } from "motion/react";
import { useCallback, useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { ArrowRight, Check, Plus, Star, X } from "lucide-react";
import { Link } from "react-router-dom";

import { AiProviderIcon } from "../AiProviderIcon";
import { EASE_OUT, staggerContainer } from "../../utils/motion";
import type { Slide, SlideTone } from "../../content/homeSlides";

// Итоговый экран в духе Apple-кейноута: мозаика плиток разного размера вокруг яркой
// центральной плитки. На плитке — короткое имя и мини-иллюстрация нововведения; полный
// текст открывается по нажатию. Раскладка задаётся `grid-template-areas` (класс `.bento` в
// index.css): имя области = `id` плитки, центральная — `hero`.

const TONE_TEXT: Record<SlideTone, string> = {
  indigo: "text-indigo-300",
  sky: "text-sky-300",
  emerald: "text-emerald-300",
  amber: "text-amber-300",
  violet: "text-violet-300",
  rose: "text-rose-300",
};

const TONE_CHIP: Record<SlideTone, string> = {
  indigo: "bg-indigo-500/15 text-indigo-300",
  sky: "bg-sky-500/15 text-sky-300",
  emerald: "bg-emerald-500/15 text-emerald-300",
  amber: "bg-amber-500/15 text-amber-300",
  violet: "bg-violet-500/15 text-violet-300",
  rose: "bg-rose-500/15 text-rose-300",
};

// ——— Мини-иллюстрации плиток ————————————————————————————————————————————————

function Pill({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full bg-white/[0.06] px-2.5 py-1 text-[11px] text-zinc-300 ${className}`}>
      {children}
    </span>
  );
}

function StepsVisual() {
  return (
    <div className="flex items-center">
      {[1, 2, 3, 4, 5].map((n) => (
        <div key={n} className="flex items-center">
          {n > 1 && <span className="h-px w-3 bg-indigo-400/40 sm:w-5" />}
          <span
            className={`flex h-8 w-8 items-center justify-center rounded-full text-sm font-semibold ${
              n === 2 ? "bg-indigo-500 text-snow shadow-[0_0_24px_rgba(99,102,241,0.6)]" : "border border-white/15 text-zinc-400"
            }`}
          >
            {n}
          </span>
        </div>
      ))}
    </div>
  );
}

const VISUALS: Record<string, () => ReactNode> = {
  bitrix: () => (
    <div className="w-full max-w-[250px] space-y-2">
      <div className="flex gap-1">
        {["Новый тендер", "Подготовка", "Подана"].map((stage, n) => (
          <span
            key={stage}
            className={`flex-1 truncate rounded-md px-1.5 py-1 text-[9.5px] ${n === 0 ? "bg-sky-500/25 text-sky-200" : "bg-white/[0.06] text-zinc-500"}`}
          >
            {stage}
          </span>
        ))}
      </div>
      <div className="rounded-xl bg-white/[0.05] p-2.5 text-left ring-1 ring-sky-400/30">
        <div className="truncate text-[11px] text-zinc-200">Поставка ПУ · 0373100…</div>
        <div className="mt-1 flex items-center justify-between text-[10px] text-zinc-500">
          <span>4,8 млн ₽</span>
          <span className="rounded-full bg-amber-500/15 px-1.5 text-amber-300">только чтение</span>
        </div>
      </div>
    </div>
  ),
  gigachat: () => (
    <div className="grid grid-cols-2 gap-1.5 text-[10.5px]">
      {(["yandex", "claude", "deepseek", "gigachat"] as const).map((provider) => (
        <span
          key={provider}
          className={`flex items-center justify-center rounded-xl p-2 ${provider === "gigachat" ? "bg-emerald-500/15 ring-1 ring-emerald-400/40" : "bg-white/[0.05] opacity-60"}`}
        >
          <AiProviderIcon provider={provider} size={20} />
        </span>
      ))}
    </div>
  ),
  "integrations-tabs": () => (
    <div className="w-full max-w-[200px]">
      <div className="flex gap-2 border-b border-white/10 text-[9.5px]">
        {["Модели ИИ", "Почта", "Bitrix24"].map((label, n) => (
          <span key={label} className={`pb-1 ${n === 0 ? "border-b-2 border-violet-400 text-zinc-100" : "text-zinc-500"}`}>
            {label}
          </span>
        ))}
      </div>
      <div className="mt-2 flex gap-1.5">
        <div className="flex-1 space-y-1">
          {[true, false, false].map((on, n) => (
            <span key={n} className={`block h-3 rounded ${on ? "bg-violet-500/40" : "bg-white/[0.07]"}`} />
          ))}
        </div>
        <span className="w-1/2 rounded bg-white/[0.05]" />
      </div>
    </div>
  ),
  profiles: () => (
    <div className="w-full max-w-[210px] space-y-1.5 text-[11px]">
      {[
        { name: "Щитовые приборы", scope: "ЕИС · Сбербанк-АСТ", on: true },
        { name: "Поверка и монтаж", scope: "ЕИС", on: true },
        { name: "Все площадки", scope: "без привязки", on: false },
      ].map((row) => (
        <div
          key={row.name}
          className={`flex items-center gap-2 rounded-lg px-2.5 py-1.5 ${row.on ? "bg-indigo-500/15 ring-1 ring-indigo-400/40" : "bg-white/[0.05]"}`}
        >
          <span className={`flex h-3.5 w-3.5 items-center justify-center rounded ${row.on ? "bg-indigo-400 text-snow" : "ring-1 ring-white/20"}`}>
            {row.on && <Check size={10} />}
          </span>
          <span className="min-w-0 flex-1 truncate text-left text-zinc-200">{row.name}</span>
          <span className="truncate text-[10px] text-zinc-500">{row.scope}</span>
        </div>
      ))}
    </div>
  ),
  "okpd2-tree": () => (
    <div className="w-full max-w-[190px] space-y-1 text-left font-mono text-[10.5px] text-zinc-300">
      <div className="flex items-center gap-1.5"><span className="h-3 w-3 rounded ring-1 ring-white/25" />26</div>
      <div className="flex items-center gap-1.5 pl-3"><span className="flex h-3 w-3 items-center justify-center rounded bg-violet-400/60 text-snow"><span className="h-0.5 w-1.5 bg-snow" /></span>26.5</div>
      <div className="flex items-center gap-1.5 pl-6"><span className="flex h-3 w-3 items-center justify-center rounded bg-violet-400 text-snow"><Check size={9} /></span>26.51</div>
      <div className="flex items-center gap-1.5 pl-6"><span className="h-3 w-3 rounded ring-1 ring-white/25" />26.52</div>
    </div>
  ),
  "tenders-header": () => (
    <div className="w-full max-w-[220px] space-y-1.5">
      <div className="flex gap-1.5">
        <span className="h-5 flex-1 rounded-md bg-white/[0.08]" />
        <span className="h-5 w-10 rounded-md bg-sky-500/40" />
      </div>
      <div className="flex gap-1.5">
        {["Ресурсы", "Профиль", "Фильтры"].map((label) => (
          <span key={label} className="rounded-md bg-white/[0.06] px-1.5 py-1 text-[9.5px] text-zinc-400">{label}</span>
        ))}
      </div>
    </div>
  ),
  minutes: () => (
    <div className="flex flex-col items-center gap-2">
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-rose-500/15 text-rose-300">
        <span className="text-lg font-semibold tabular-nums">0 д</span>
      </span>
      <span className="text-[11px] text-zinc-500">срок = день размещения</span>
    </div>
  ),
  "catalog-steps": StepsVisual,
  catalog: StepsVisual,
  manual: () => (
    <div className="flex h-20 w-28 flex-col items-center justify-center gap-1 rounded-2xl border border-dashed border-rose-300/40 text-rose-300">
      <Plus size={22} />
      <span className="text-[10px] text-zinc-400">файл документации</span>
    </div>
  ),
  tenders: () => (
    <div className="w-full max-w-[210px] space-y-1.5">
      {[0, 1, 2, 3].map((n) => (
        <div key={n} className="flex items-center gap-2 rounded-lg bg-white/[0.05] px-2.5 py-2">
          <span className={`h-1.5 w-1.5 rounded-full ${n === 1 ? "bg-zinc-600" : "bg-sky-400"}`} />
          <span className="h-1.5 flex-1 rounded-full bg-white/15" style={{ maxWidth: `${90 - n * 12}%` }} />
          <Star size={11} className={n === 0 ? "fill-amber-400 text-amber-400" : "text-zinc-700"} />
        </div>
      ))}
      <div className="flex items-center gap-1.5 pt-1 text-[10px] text-zinc-400">
        <span className="flex h-3 w-3 items-center justify-center rounded-[3px] bg-indigo-500">
          <Check size={9} className="text-snow" />
        </span>
        Только подобранные ИИ
      </div>
    </div>
  ),
  "tender-card": () => (
    <div className="w-full max-w-[210px] rounded-2xl bg-white/[0.05] p-3">
      <div className="flex gap-1 text-[10px]">
        <span className="rounded-md bg-white/10 px-1.5 py-0.5 text-zinc-100">Требования</span>
        <span className="px-1.5 py-0.5 text-zinc-500">Соответствие</span>
      </div>
      <div className="mt-3 space-y-1.5">
        {["bg-emerald-400", "bg-emerald-400", "bg-rose-400"].map((dot, n) => (
          <div key={n} className="flex items-center gap-2">
            <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
            <span className="h-1.5 flex-1 rounded-full bg-white/15" />
          </div>
        ))}
      </div>
      <div className="mt-3 rounded-full bg-emerald-500 py-1.5 text-center text-[10px] font-medium text-snow">Разобрать закупку</div>
    </div>
  ),
  company: () => (
    <div className="flex flex-col items-center gap-1.5">
      <Pill className="bg-violet-500/10 text-violet-200">Профиль компании</Pill>
      <Pill>История участий</Pill>
    </div>
  ),
  analytics: () => (
    <div className="flex h-16 items-end gap-1.5">
      {[40, 65, 50, 85, 60, 100].map((h, n) => (
        <span key={n} className="w-3 rounded-t-md bg-gradient-to-t from-sky-600/60 to-sky-300" style={{ height: `${h}%` }} />
      ))}
    </div>
  ),
  settings: () => (
    <div className="w-full max-w-[260px] space-y-2">
      {[
        ["Счётчики электроэнергии", true],
        ["АСКУЭ и УСПД", true],
        ["Трансформаторы тока", false],
      ].map(([label, on]) => (
        <div key={String(label)} className="flex items-center justify-between rounded-xl bg-white/[0.05] px-3 py-2 text-[11px] text-zinc-300">
          {label}
          <span className={`flex h-4 w-7 items-center rounded-full p-0.5 ${on ? "justify-end bg-amber-400" : "bg-white/15"}`}>
            <span className="h-3 w-3 rounded-full bg-white" />
          </span>
        </div>
      ))}
    </div>
  ),
  done: () => (
    <span className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-300">
      <Check size={30} />
    </span>
  ),
};

// ——— Описание по нажатию ————————————————————————————————————————————————————

function SlideDetails({ slide, onClose }: { slide: Slide; onClose: () => void }) {
  const Icon = slide.icon;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="keynote-fade fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={slide.title}
        onClick={(e) => e.stopPropagation()}
        className="keynote-rise relative max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-3xl border border-white/10 bg-zinc-900 p-8 shadow-[0_20px_60px_rgba(0,0,0,0.5)]"
      >
        <button
          onClick={onClose}
          title="Закрыть (Esc)"
          className="absolute right-4 top-4 rounded-full p-2 text-zinc-500 hover:bg-white/5 hover:text-zinc-200"
        >
          <X size={18} />
        </button>
        <div className={`mb-5 flex h-11 w-11 items-center justify-center rounded-2xl ${TONE_CHIP[slide.tone]}`}>
          <Icon size={22} />
        </div>
        <div className={`text-xs font-medium uppercase tracking-wider ${TONE_TEXT[slide.tone]}`}>{slide.kicker}</div>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight text-zinc-50">{slide.title}</h2>
        <p className="mt-2 text-[15px] text-zinc-400">{slide.lead}</p>
        <div className="mt-5 space-y-3 text-[15px] leading-relaxed text-zinc-300">
          {slide.details.map((paragraph) => (
            <p key={paragraph}>{paragraph}</p>
          ))}
        </div>
        {slide.link && (
          <Link
            to={slide.link.to}
            className="mt-7 inline-flex items-center gap-2 rounded-full bg-indigo-500 px-4 py-2 text-sm font-medium text-snow hover:bg-indigo-400"
          >
            {slide.link.label} <ArrowRight size={15} />
          </Link>
        )}
      </div>
    </div>
  );
}

// ——— Мозаика ————————————————————————————————————————————————————————————————

// Плитки проявляются каскадом, центральная — первой и с лёгким «вдохом» масштаба.
const tileVariants: Variants = {
  initial: { opacity: 0, y: 14, scale: 0.97 },
  animate: { opacity: 1, y: 0, scale: 1, transition: { duration: 0.45, ease: EASE_OUT } },
};
const heroVariants: Variants = {
  initial: { opacity: 0, scale: 0.92 },
  animate: { opacity: 1, scale: 1, transition: { type: "spring", bounce: 0.2, duration: 0.7 } },
};

function Tile({ slide, onOpen }: { slide: Slide; onOpen: (slide: Slide) => void }) {
  const Visual = VISUALS[slide.id];
  const Icon = slide.icon;
  return (
    <motion.button
      variants={tileVariants}
      whileHover={{ y: -3 }}
      whileTap={{ scale: 0.98 }}
      onClick={() => onOpen(slide)}
      style={{ "--area": slide.id } as CSSProperties}
      aria-label={`${slide.tile}. Открыть описание`}
      className="bento-tile group relative flex min-h-[180px] flex-col items-center overflow-hidden rounded-[28px] border border-white/[0.07] bg-zinc-900/70 px-5 pb-5 pt-5 text-center transition-colors duration-300 hover:border-white/15 hover:bg-zinc-900"
    >
      <span className="text-[15px] font-semibold leading-snug tracking-tight text-zinc-100">{slide.tile}</span>
      <span className="flex w-full flex-1 items-center justify-center pt-3">
        {Visual ? (
          <Visual />
        ) : (
          <span className={`flex h-14 w-14 items-center justify-center rounded-2xl ${TONE_CHIP[slide.tone]}`}>
            <Icon size={26} />
          </span>
        )}
      </span>
      <span className="absolute bottom-3 right-3 flex h-7 w-7 items-center justify-center rounded-full bg-white/[0.06] text-zinc-400 opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
        <Plus size={15} />
      </span>
    </motion.button>
  );
}

export function BentoRecap({
  hero,
  heroSlide,
  slides,
  areas,
}: {
  hero: { title: string; subtitle: string };
  /** Что открывается по нажатию на центральную плитку; без него она не нажимается. */
  heroSlide?: Slide;
  slides: Slide[];
  /** `grid-template-areas` на широком экране: четыре колонки, область центральной плитки — `hero`. */
  areas: string[];
}) {
  const [openSlide, setOpenSlide] = useState<Slide | null>(null);
  const closeDetails = useCallback(() => setOpenSlide(null), []);

  const heroContent = (
    <>
      {/* Переливающийся градиент, как у центральной плитки итогов WWDC */}
      <span className="bento-hero-gradient absolute inset-0" />
      <span className="absolute -left-10 top-6 h-40 w-40 rounded-full bg-[#bae6fd]/50 blur-3xl" />
      <span className="absolute -bottom-10 right-4 h-44 w-44 rounded-full bg-[#fda4af]/50 blur-3xl" />
      <span className="relative text-center">
        <span className="block text-5xl font-semibold tracking-tight text-snow drop-shadow-sm xl:text-6xl">{hero.title}</span>
        <span className="mt-3 block text-sm font-medium text-snow/80">{hero.subtitle}</span>
      </span>
    </>
  );
  // Две колонки — только до 1024px: Tailwind выводит `sm:`-варианты в конце таблицы стилей,
  // и `sm:grid-cols-2` перебивал четыре колонки `.bento` — колонки мозаики выходили разной ширины.
  const heroClass =
    "bento-tile relative flex min-h-[260px] items-center justify-center overflow-hidden rounded-[28px] sm:max-lg:col-span-2";

  return (
    <>
      <motion.div
        variants={staggerContainer}
        initial="initial"
        animate="animate"
        className="bento grid grid-cols-1 gap-3 sm:max-lg:grid-cols-2"
        style={{ "--bento-areas": areas.map((row) => `"${row}"`).join(" ") } as CSSProperties}
      >
        {heroSlide ? (
          <motion.button
            variants={heroVariants}
            whileHover={{ y: -3 }}
            whileTap={{ scale: 0.99 }}
            onClick={() => setOpenSlide(heroSlide)}
            aria-label={`${hero.title}. Открыть описание`}
            style={{ "--area": "hero" } as CSSProperties}
            className={heroClass}
          >
            {heroContent}
          </motion.button>
        ) : (
          <motion.div variants={heroVariants} style={{ "--area": "hero" } as CSSProperties} className={heroClass}>
            {heroContent}
          </motion.div>
        )}
        {slides.map((slide) => (
          <Tile key={slide.id} slide={slide} onOpen={setOpenSlide} />
        ))}
      </motion.div>
      {openSlide && <SlideDetails slide={openSlide} onClose={closeDetails} />}
    </>
  );
}
