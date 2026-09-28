import { motion } from "motion/react";
import type { Variants } from "motion/react";
import { useCallback, useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { ArrowRight, Check, Download, FileText, Moon, Plus, Search, Star, Sun, X } from "lucide-react";
import { Link } from "react-router-dom";

import { EASE_OUT, staggerContainer } from "../../utils/motion";
import type { Slide, SlideTone } from "../../content/homeSlides";
import { AiProviderIcon } from "../AiProviderIcon";
import { LogoMark } from "../Logo";

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
  "ai-conclusion": () => (
    <div className="w-full max-w-[340px] rounded-2xl bg-white/[0.05] p-3.5 text-left">
      <div className="flex items-center gap-2">
        <span className="rounded-full bg-emerald-500/15 px-2.5 py-1 text-[11px] font-semibold text-emerald-300">ПОДХОДИМ</span>
        <span className="text-[11px] text-zinc-400">с прибором МИРТЕК-12-РУ</span>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-1.5 text-[10px]">
        {[
          ["Наши приборы", "2 подходят", "text-emerald-300"],
          ["Конкуренты", "3 проходят", "text-sky-300"],
          ["Риски", "4 · цена", "text-amber-300"],
        ].map(([label, value, tone]) => (
          <div key={label} className="rounded-lg bg-white/[0.05] px-2 py-1.5">
            <div className="text-zinc-500">{label}</div>
            <div className={`mt-0.5 font-medium ${tone}`}>{value}</div>
          </div>
        ))}
      </div>
    </div>
  ),
  "specialist-feedback": () => (
    <div className="flex w-full max-w-[200px] flex-col gap-2 text-[11px]">
      <div className="flex gap-1.5">
        <span className="flex-1 rounded-lg bg-white/[0.05] py-1.5 text-center text-zinc-400">Согласен</span>
        <span className="flex-1 rounded-lg bg-sky-500/20 py-1.5 text-center text-sky-200 ring-1 ring-sky-400/40">Не согласен</span>
      </div>
      <div className="rounded-lg bg-white/[0.05] px-2.5 py-2 text-zinc-300">Реле у модели есть — см. РЭ, п. 4.2</div>
      <div className="flex items-center gap-1.5 text-zinc-500">
        <span className="text-rose-300 line-through">не подходит</span>→<span className="text-emerald-300">подходит</span>
      </div>
    </div>
  ),
  "review-no-gaps": () => (
    <div className="flex items-center">
      {["Документы", "Матрица", "Заключение"].map((label, n) => (
        <div key={label} className="flex items-center">
          {n > 0 && <span className="h-px w-3 bg-indigo-400/40" />}
          <span className="flex flex-col items-center gap-1">
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-500/20 text-indigo-200 ring-1 ring-indigo-400/40">
              <Check size={14} />
            </span>
            <span className="text-[10px] text-zinc-400">{label}</span>
          </span>
        </div>
      ))}
    </div>
  ),
  "catalog-autofill": () => (
    <div className="w-full max-w-[300px] space-y-1.5 text-[12px]">
      {(
        [
          ["Энергомера", "251 модель", "done"],
          ["МИРТЕК", "38 моделей", "done"],
          ["Нартис", "опрашивается…", "busy"],
        ] as const
      ).map(([name, value, state]) => (
        <div key={name} className="flex items-center gap-2 rounded-lg bg-white/[0.05] px-3 py-1.5">
          <span className="w-24 truncate text-zinc-200">{name}</span>
          <span className="text-[11px] text-zinc-500">{value}</span>
          {state === "done" ? (
            <Check size={13} className="ml-auto text-emerald-400" />
          ) : (
            <Search size={13} className="catalog-searching ml-auto text-violet-300" />
          )}
        </div>
      ))}
      <div className="pt-1 text-center text-[11px] text-zinc-500">каждую ночь в 01:00</div>
    </div>
  ),
  "si-groups": () => (
    <div className="w-full max-w-[200px] space-y-1.5 text-[11px]">
      {(
        [
          ["Ждут подтверждения", 4, "bg-amber-400"],
          ["Действующие", 21, "bg-emerald-400"],
          ["Свидетельство истекло", 3, "bg-rose-400"],
          ["Не электросчётчики", 1, "bg-zinc-500"],
        ] as const
      ).map(([label, count, dot]) => (
        <div key={label} className="flex items-center gap-2 rounded-lg bg-white/[0.05] px-2.5 py-1.5 text-zinc-300">
          <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
          {label}
          <span className="ml-auto font-mono tabular-nums text-zinc-500">{count}</span>
        </div>
      ))}
    </div>
  ),
  gosplan: () => (
    <div className="flex rounded-full bg-white/[0.06] p-1 text-sm">
      <span className="rounded-full px-4 py-1.5 text-zinc-500">Стандартные ресурсы</span>
      <span className="rounded-full bg-sky-500/20 px-4 py-1.5 font-medium text-sky-200 ring-1 ring-sky-400/40">Госплан</span>
    </div>
  ),
  "tenders-clean": () => (
    <div className="flex w-full max-w-[210px] gap-1.5">
      <div className="w-16 space-y-1">
        {[0, 1, 2, 3].map((n) => (
          <span key={n} className={`block h-3 rounded ${n === 0 ? "border-l-2 border-indigo-400 bg-white/10" : "bg-white/[0.05]"}`} />
        ))}
      </div>
      <div className="flex-1 rounded-lg bg-white/[0.05] p-2">
        <span className="block h-2 w-3/4 rounded-full bg-white/25" />
        <span className="mt-1.5 block h-1.5 w-1/2 rounded-full bg-white/10" />
        <span className="mt-3 block h-8 rounded-md bg-indigo-500/15" />
      </div>
    </div>
  ),
  "nav-layout": () => (
    <div className="flex flex-col items-center gap-3">
      {[false, true].map((header) => (
        <div
          key={String(header)}
          className={`flex h-16 w-28 overflow-hidden rounded-lg ring-1 ${header ? "flex-col ring-violet-400/50" : "ring-white/10"}`}
        >
          <span className={header ? "h-3 bg-violet-500/40" : "w-6 bg-white/10"} />
          <span className="flex-1 bg-white/[0.04]" />
        </div>
      ))}
      <span className="text-[11px] text-zinc-500">сбоку или в шапке</span>
    </div>
  ),
  theme: () => (
    <div className="flex flex-col items-center gap-3">
      <div className="flex h-24 w-20 overflow-hidden rounded-xl ring-1 ring-white/10">
        <span className="flex flex-1 items-center justify-center bg-scrim text-snow">
          <Moon size={16} />
        </span>
        <span className="flex flex-1 items-center justify-center bg-snow text-amber-500">
          <Sun size={16} />
        </span>
      </div>
      <span className="text-[11px] text-zinc-500">Тёмная · Светлая</span>
    </div>
  ),
  "settings-tabs": () => (
    <div className="w-full max-w-[200px] space-y-2">
      <div className="flex items-center gap-2 rounded-lg bg-white/[0.06] px-2.5 py-1.5 text-[11px] text-zinc-500">
        <Search size={12} /> Поиск по настройкам
        <span className="ml-auto rounded bg-white/10 px-1 font-mono text-[10px] text-zinc-400">⌘K</span>
      </div>
      <div className="flex flex-wrap gap-1">
        {["Источники", "Госплан", "Профиль", "Интерфейс"].map((tab, n) => (
          <span key={tab} className={`rounded-md px-1.5 py-0.5 text-[10px] ${n === 0 ? "bg-indigo-500/20 text-indigo-200" : "text-zinc-500"}`}>
            {tab}
          </span>
        ))}
      </div>
    </div>
  ),
  "logs-page": () => (
    <div className="flex flex-col items-center gap-2">
      <div className="flex gap-1">
        {["За час", "За сутки", "За неделю"].map((label) => (
          <span key={label} className="rounded-md bg-white/[0.06] px-2 py-1 text-[10px] text-zinc-300">
            {label}
          </span>
        ))}
      </div>
      <span className="flex items-center gap-1.5 rounded-lg bg-rose-500/15 px-3 py-1.5 font-mono text-[10.5px] text-rose-200">
        <Download size={12} /> sova-log_1h.txt
      </span>
    </div>
  ),
  "seed-snapshot": () => (
    <div className="w-full max-w-[200px] rounded-xl bg-black/60 p-3 text-left font-mono text-[10.5px] leading-relaxed ring-1 ring-white/10">
      <div className="text-zinc-300">$ ./install.sh</div>
      <div className="text-emerald-400">✓ каталог и компании</div>
      <div className="text-emerald-400">✓ матрицы</div>
      <div className="text-emerald-400">✓ rusprofile в фоне</div>
    </div>
  ),
  "sidebar-brand": () => (
    <div className="flex items-center gap-4">
      <span className="brand-gradient relative flex h-[52px] w-44 items-center gap-3 overflow-hidden rounded-xl pl-[14px] text-snow">
        <LogoMark size={18} className="text-snow drop-shadow" />
        <span className="brand-wordmark text-[17px] leading-none">Sova</span>
      </span>
      <span className="h-px w-5 bg-white/15" />
      <span className="brand-gradient relative flex h-[52px] w-[52px] items-center justify-center overflow-hidden rounded-xl text-snow">
        <LogoMark size={18} className="text-snow drop-shadow" />
      </span>
    </div>
  ),
  "catalog-sort": () => (
    <div className="w-full max-w-[200px] space-y-1.5 text-[12px]">
      <div className="mb-2.5 flex justify-center gap-1">
        <span className="rounded-md bg-white/10 px-2 py-0.5 text-[10px] text-zinc-100">По доле</span>
        <span className="rounded-md px-2 py-0.5 text-[10px] text-zinc-500">А–Я</span>
      </div>
      {[
        ["Нартис", 26],
        ["Энергомера", 23],
        ["Waviot", 13],
      ].map(([name, pct]) => (
        <div key={name} className="flex items-center gap-2">
          <span className="w-20 truncate text-zinc-300">{name}</span>
          <span className="h-1.5 rounded-full bg-sky-400/70" style={{ width: `${Number(pct) * 3}px` }} />
          <span className="ml-auto font-mono text-[10px] tabular-nums text-zinc-500">{pct} %</span>
        </div>
      ))}
    </div>
  ),
  "catalog-steps": StepsVisual,
  catalog: StepsVisual,
  "meter-parameters": () => (
    <div className="flex items-center gap-6">
      <span className="bg-gradient-to-br from-emerald-200 via-emerald-400 to-teal-600 bg-clip-text text-[88px] font-semibold leading-none tracking-tighter text-transparent">
        39
      </span>
      <div className="grid grid-cols-8 gap-1">
        {Array.from({ length: 39 }, (_, i) => (
          <span key={i} className={`h-2.5 w-2.5 rounded-full ${i % 7 === 5 ? "bg-white/10" : "bg-emerald-400/80"}`} />
        ))}
      </div>
    </div>
  ),
  "ai-models": () => (
    <div className="flex flex-col items-center gap-3">
      {(
        [
          ["claude", "Claude"],
          ["deepseek", "DeepSeek"],
          ["yandex", "YandexGPT"],
        ] as const
      ).map(([key, label], i) => (
        <div
          key={key}
          className={`flex w-40 items-center gap-3 rounded-2xl px-3 py-2.5 ${
            i === 0 ? "bg-white/[0.09] ring-1 ring-orange-300/40" : "bg-white/[0.04]"
          }`}
        >
          <AiProviderIcon provider={key} size={28} />
          <span className="text-sm text-zinc-200">{label}</span>
          {i === 0 && <Check size={14} className="ml-auto text-orange-300" />}
        </div>
      ))}
    </div>
  ),
  "ai-checklists": () => (
    <div className="w-full max-w-[200px] space-y-2">
      {(
        [
          ["Задача", "bg-emerald-400"],
          ["Компетенции", "bg-emerald-400"],
          ["История", "bg-amber-400"],
        ] as const
      ).map(([label, dot]) => (
        <div key={label} className="rounded-xl bg-white/[0.05] px-3 py-2">
          <div className="text-[11px] font-medium text-zinc-300">{label}</div>
          <div className="mt-1.5 flex gap-1">
            {[0, 1, 2, 3, 4].map((n) => (
              <span key={n} className={`h-1.5 flex-1 rounded-full ${n < 4 ? dot : "bg-white/10"}`} />
            ))}
          </div>
        </div>
      ))}
      <div className="pt-1 text-center text-[11px] text-zinc-500">итог считает код</div>
    </div>
  ),
  "platforms-fixed": () => (
    <div className="flex flex-wrap items-center justify-center gap-3">
      {["ТЭК-Торг", "ZakazRF"].map((name) => (
        <span key={name} className="flex items-center gap-2.5 rounded-2xl bg-white/[0.06] px-5 py-3 text-lg font-medium text-zinc-100">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-400" />
          </span>
          {name}
        </span>
      ))}
    </div>
  ),
  relevance: () => (
    <div className="space-y-1.5 text-[12px]">
      <div className="text-zinc-600 line-through decoration-rose-400/70">Бумага для заметок</div>
      <div className="text-zinc-600 line-through decoration-rose-400/70">Замена светильников</div>
      <div className="flex items-center gap-1.5 text-zinc-100">
        <Check size={13} className="text-emerald-400" /> Счётчики электроэнергии
      </div>
    </div>
  ),
  "full-review": () => (
    <div className="flex w-full max-w-[190px] flex-col items-center gap-3">
      <span className="rounded-full bg-emerald-500 px-4 py-2 text-xs font-medium text-snow shadow-[0_0_30px_rgba(16,185,129,0.45)]">
        Разобрать закупку
      </span>
      <span className="h-1 w-full overflow-hidden rounded-full bg-white/10">
        <span className="block h-full w-2/3 rounded-full bg-gradient-to-r from-emerald-400 to-sky-400" />
      </span>
    </div>
  ),
  "manual-requests": () => (
    <div className="relative">
      <div className="flex h-20 w-16 items-center justify-center rounded-xl border border-white/10 bg-white/[0.06]">
        <FileText size={28} className="text-rose-300" strokeWidth={1.5} />
      </div>
      <Star size={22} className="absolute -right-3 -top-2 fill-amber-400 text-amber-400" />
    </div>
  ),
  manual: () => (
    <div className="flex h-20 w-28 flex-col items-center justify-center gap-1 rounded-2xl border border-dashed border-rose-300/40 text-rose-300">
      <Plus size={22} />
      <span className="text-[10px] text-zinc-400">файл документации</span>
    </div>
  ),
  "registry-learning": () => (
    <div className="flex max-w-[340px] flex-wrap justify-center gap-1.5">
      {["Пирамида", "Энфорс", "Энергосфера", "яЭнергетик", "АльфаЦЕНТР", "Некта", "ЛЭРС"].map((name) => (
        <Pill key={name} className="bg-violet-500/10 text-violet-200">
          {name}
        </Pill>
      ))}
    </div>
  ),
  "server-update": () => (
    <div className="w-full max-w-[200px] rounded-xl bg-black/60 p-3 text-left font-mono text-[10.5px] leading-relaxed ring-1 ring-white/10">
      <div className="text-zinc-300">$ ./update.sh</div>
      <div className="text-emerald-400">✓ дамп базы</div>
      <div className="text-emerald-400">✓ миграции</div>
      <div className="text-emerald-400">✓ перезапуск</div>
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
  const heroClass =
    "bento-tile relative flex min-h-[260px] items-center justify-center overflow-hidden rounded-[28px] sm:col-span-2";

  return (
    <>
      <motion.div
        variants={staggerContainer}
        initial="initial"
        animate="animate"
        className="bento grid grid-cols-1 gap-3 sm:grid-cols-2"
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
