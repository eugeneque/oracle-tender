import { motion } from "motion/react";
import type { Variants } from "motion/react";
import { useCallback, useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { ArrowRight, Check, Clock3, Plus, Search, Star, X, Zap } from "lucide-react";
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
  "ai-usage": () => (
    <div className="w-full max-w-[200px] space-y-3">
      <div className="flex h-16 items-end gap-1">
        {[35, 55, 30, 80, 45, 100, 60].map((h, n) => (
          <span
            key={n}
            className={`flex-1 rounded-t-[4px] ${n === 5 ? "bg-violet-400" : "bg-violet-500/30"}`}
            style={{ height: `${h}%` }}
          />
        ))}
      </div>
      <div className="space-y-1.5 text-[10.5px]">
        {[
          ["Разбор документации", "62%"],
          ["Матрица соответствия", "27%"],
          ["Каталог", "11%"],
        ].map(([label, share]) => (
          <div key={label} className="flex items-center justify-between rounded-lg bg-white/[0.05] px-2.5 py-1.5">
            <span className="truncate text-zinc-300">{label}</span>
            <span className="tabular-nums text-violet-300">{share}</span>
          </div>
        ))}
      </div>
      <div className="text-[10px] text-zinc-500">прогноз на месяц · 4,1 млн токенов</div>
    </div>
  ),
  "ai-errors": () => (
    <div className="w-full max-w-[210px] space-y-1.5 text-left text-[10.5px]">
      {[
        ["Таймаут", "2 из 3", "bg-amber-400"],
        ["Не по схеме", "повтор", "bg-amber-400"],
        ["Лимит", "отказ", "bg-rose-400"],
      ].map(([label, note, dot]) => (
        <div key={label} className="flex items-center gap-2 rounded-lg bg-white/[0.05] px-2.5 py-1.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
          <span className="min-w-0 flex-1 truncate text-zinc-200">{label}</span>
          <span className="truncate text-[9.5px] text-zinc-500">{note}</span>
        </div>
      ))}
    </div>
  ),
  "analyzed-only": () => (
    <div className="flex flex-col items-center gap-2.5">
      <div className="flex items-end gap-4">
        {[
          ["128", "в списке", "text-zinc-100"],
          ["34", "разобрано", "text-emerald-300"],
        ].map(([count, label, tone]) => (
          <span key={label} className="flex flex-col items-center">
            <span className={`text-2xl font-semibold tabular-nums ${tone}`}>{count}</span>
            <span className="text-[10px] text-zinc-500">{label}</span>
          </span>
        ))}
      </div>
      <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-3 py-1 text-[11px] text-emerald-200 ring-1 ring-emerald-400/40">
        <Check size={11} /> Разобранные
      </span>
    </div>
  ),
  "model-sources": () => (
    <div className="flex w-full max-w-[420px] gap-2.5 text-left text-[10.5px]">
      <div className="flex-1 space-y-1">
        {[
          ["Ток макс.", "80 А", "сайт", false],
          ["Класс точности", "1/1", "ГРСИ", false],
          ["Степень защиты", "IP54", "ИИ", true],
          ["Интерфейсы", "RS-485, NB-IoT", "сайт", false],
        ].map(([label, value, source, unchecked]) => (
          <div key={String(label)} className="flex items-center gap-2 rounded-lg bg-white/[0.05] px-2.5 py-1.5">
            <span className="min-w-0 flex-1 truncate text-zinc-500">{label}</span>
            <span className="flex items-center gap-1 text-zinc-100">
              {value}
              {unchecked && <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />}
            </span>
            <span className="w-9 shrink-0 text-right text-[9.5px] text-amber-300/80">{source}</span>
          </div>
        ))}
      </div>
      <div className="hidden w-[42%] flex-col gap-1.5 rounded-xl bg-white/[0.04] p-2.5 ring-1 ring-amber-400/25 sm:flex">
        <span className="text-[10px] font-medium text-zinc-300">Источники данных</span>
        <span className="flex items-center gap-1.5 text-zinc-400"><Check size={10} className="text-emerald-300" />Сайт производителя</span>
        <span className="flex items-center gap-1.5 text-zinc-400"><Check size={10} className="text-emerald-300" />Описание типа</span>
        <span className="flex items-center gap-1.5 text-zinc-500"><X size={10} className="text-rose-300" />Руководство — загрузите PDF</span>
      </div>
    </div>
  ),
  "top-search": () => (
    <div className="w-full max-w-[200px] space-y-2 text-left">
      <div className="flex items-center gap-2 rounded-full bg-white/[0.08] px-3 py-1.5 text-[11px] text-zinc-200 ring-1 ring-sky-400/40">
        <Search size={12} className="text-sky-300" />
        счетчик
      </div>
      {["Поставка счётчиков ЭЭ", "Счётчики АСКУЭ, монтаж"].map((title) => (
        <div key={title} className="truncate rounded-lg bg-white/[0.05] px-2.5 py-1.5 text-[10.5px] text-zinc-400">
          {title.split(/(Счётчик|счётчик)/).map((part, n) =>
            /ётчик/.test(part) ? <mark key={n} className="rounded-sm bg-sky-400/25 text-sky-100">{part}</mark> : part,
          )}
        </div>
      ))}
    </div>
  ),
  schedule: () => (
    <div className="flex w-full max-w-[200px] flex-col items-center gap-2.5">
      <div className="flex gap-2">
        {["06:00", "20:00"].map((time) => (
          <span key={time} className="flex items-center gap-1.5 rounded-xl bg-indigo-500/15 px-2.5 py-1.5 text-sm font-semibold tabular-nums text-indigo-200">
            <Clock3 size={13} /> {time}
          </span>
        ))}
      </div>
      <div className="relative h-1.5 w-full rounded-full bg-white/[0.07]">
        <span className="absolute -top-0.5 h-2.5 w-2.5 rounded-full bg-indigo-400" style={{ left: "25%" }} />
        <span className="absolute -top-0.5 h-2.5 w-2.5 rounded-full bg-indigo-400" style={{ left: "83%" }} />
      </div>
      <span className="text-[10px] text-zinc-500">по Москве · запуск и итог — в журнале</span>
    </div>
  ),
  "requirements-view": () => (
    <div className="flex w-full max-w-[420px] gap-2.5 text-left text-[10.5px]">
      <div className="flex-1 space-y-1">
        <span className="block text-[9.5px] uppercase tracking-wider text-sky-300/80">Метрология</span>
        {["Класс точности 1/2", "Межповерочный интервал 16 лет"].map((text, n) => (
          <div key={text} className="flex items-center gap-2 rounded-lg bg-white/[0.05] px-2 py-1.5">
            <span className="w-3 text-right tabular-nums text-zinc-500">{n + 1}</span>
            <span className="truncate text-zinc-200">{text}</span>
          </div>
        ))}
        <span className="block pt-0.5 text-[9.5px] uppercase tracking-wider text-sky-300/80">Связь</span>
        <div className="flex items-center gap-2 rounded-lg bg-white/[0.05] px-2 py-1.5">
          <span className="w-3 text-right tabular-nums text-zinc-500">3</span>
          <span className="truncate text-zinc-200">RS-485, оптопорт</span>
        </div>
      </div>
      <div className="hidden flex-1 overflow-hidden rounded-xl bg-white/[0.04] sm:block">
        <div className="grid grid-cols-[1fr_auto_auto] gap-x-2 gap-y-1 p-2 text-[10px]">
          <span className="text-zinc-500">Требование</span>
          <span className="text-zinc-300">МИРТЕК</span>
          <span className="text-zinc-300">Энергомера</span>
          {[
            ["1", "bg-emerald-400", "bg-emerald-400"],
            ["2", "bg-emerald-400", "bg-amber-400"],
            ["3", "bg-emerald-400", "bg-rose-400"],
          ].map(([n, a, b]) => (
            <div key={n} className="contents">
              <span className="text-zinc-500">№ {n}</span>
              <span className="flex justify-center"><span className={`mt-1 h-1.5 w-1.5 rounded-full ${a}`} /></span>
              <span className="flex justify-center"><span className={`mt-1 h-1.5 w-1.5 rounded-full ${b}`} /></span>
            </div>
          ))}
          <span className="pt-1 text-zinc-500">Итог</span>
          <span className="pt-1 text-center tabular-nums text-emerald-300">100%</span>
          <span className="pt-1 text-center tabular-nums text-amber-300">67%</span>
        </div>
      </div>
    </div>
  ),
  registries: () => (
    <div className="flex flex-col items-center gap-1.5">
      {["ГИСП · ПП 719", "ЗАК Россетей"].map((label) => (
        <span key={label} className="flex items-center gap-2 rounded-full bg-emerald-500/10 px-3 py-1.5 text-[11px] text-emerald-200 ring-1 ring-emerald-400/30">
          <span className="flex h-3.5 w-3.5 items-center justify-center rounded-full bg-emerald-400 text-snow"><Check size={9} /></span>
          {label}
        </span>
      ))}
      <span className="pt-1 text-[10px] text-zinc-500">сверка раз в неделю</span>
    </div>
  ),
  "catalog-lean": () => (
    <div className="w-full max-w-[190px] space-y-2 text-left text-[10px]">
      <div>
        <div className="mb-1 flex justify-between text-zinc-500"><span>было</span><span className="tabular-nums">≈ 900 тыс.</span></div>
        <span className="block h-2 w-full rounded-full bg-white/15" />
      </div>
      <div>
        <div className="mb-1 flex justify-between text-zinc-300"><span>стало</span><span className="tabular-nums text-emerald-300">≈ 90 тыс.</span></div>
        <span className="block h-2 w-[12%] rounded-full bg-emerald-400" />
      </div>
      <span className="block pt-0.5 text-zinc-500">токенов в неделю на каталог</span>
    </div>
  ),
  "marked-lists": () => (
    <div className="grid w-full max-w-[400px] grid-cols-2 gap-2.5 text-left text-[10.5px]">
      {[
        { label: "Релевантные", count: 12, dot: "bg-emerald-400", ring: "ring-emerald-400/30" },
        { label: "Неактуальные", count: 5, dot: "bg-zinc-500", ring: "ring-white/10" },
      ].map((list) => (
        <div key={list.label} className={`rounded-xl bg-white/[0.04] p-2.5 ring-1 ${list.ring}`}>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-zinc-200">{list.label}</span>
            <span className="tabular-nums text-zinc-500">{list.count}</span>
          </div>
          {[80, 62, 70].map((w) => (
            <div key={w} className="flex items-center gap-1.5 py-1">
              <span className={`h-1.5 w-1.5 rounded-full ${list.dot}`} />
              <span className="h-1.5 rounded-full bg-white/15" style={{ width: `${w}%` }} />
            </div>
          ))}
        </div>
      ))}
    </div>
  ),
  "tz-reference": () => (
    <div className="w-full max-w-[380px] space-y-1.5 text-left text-[10.5px]">
      {[
        { name: "МИРТЕК-32-РУ", note: "эталон ТЗ", pct: "100%", cls: "bg-emerald-500/15 ring-1 ring-emerald-400/40", tone: "text-emerald-300" },
        { name: "Энергомера CE308", note: "эквивалент · с оговорками", pct: "74%", cls: "bg-white/[0.05]", tone: "text-amber-300" },
        { name: "Матрица NP73", note: "не проходит", pct: "41%", cls: "bg-white/[0.05]", tone: "text-rose-300" },
      ].map((row) => (
        <div key={row.name} className={`flex items-center gap-2 rounded-lg px-2.5 py-1.5 ${row.cls}`}>
          <span className="min-w-0 flex-1 truncate text-zinc-200">{row.name}</span>
          <span className="truncate text-[9.5px] text-zinc-500">{row.note}</span>
          <span className={`w-9 text-right tabular-nums ${row.tone}`}>{row.pct}</span>
        </div>
      ))}
    </div>
  ),
  selection: () => (
    <div className="w-full max-w-[380px] space-y-1.5 text-left text-[10.5px]">
      {[
        ["Собрано", "2 140", 100],
        ["Профили", "312", 46],
        ["Модель", "96", 24],
        ["В списке", "41", 12],
      ].map(([label, count, w], n) => (
        <div key={String(label)} className="flex items-center gap-2">
          <span className="w-16 shrink-0 text-zinc-500">{label}</span>
          <span className="h-4 flex-1">
            <span
              className={`flex h-full items-center justify-end rounded-md pr-1.5 text-[9.5px] tabular-nums ${n === 3 ? "bg-indigo-400 text-snow" : "bg-indigo-500/25 text-indigo-200"}`}
              style={{ width: `${Math.max(Number(w), 14)}%` }}
            >
              {count}
            </span>
          </span>
        </div>
      ))}
    </div>
  ),
  "brand-lock": () => (
    <div className="w-full max-w-[360px] space-y-2 text-left">
      <div className="rounded-xl bg-white/[0.05] p-2.5 text-[10.5px] leading-relaxed text-zinc-400">
        Счётчик <span className="rounded bg-rose-400/20 px-1 text-rose-200">Энергомера CE208</span> — 450 шт.,{" "}
        <span className="rounded bg-rose-400/20 px-1 text-rose-200">эквивалент не допускается</span>
      </div>
      <span className="inline-flex items-center gap-1.5 rounded-full bg-rose-500/15 px-2.5 py-1 text-[10.5px] text-rose-200">
        <X size={11} /> ТЗ под чужой товарный знак
      </span>
    </div>
  ),
  feeds: () => (
    <div className="w-full max-w-[180px] space-y-1.5 text-left text-[11px]">
      {[
        ["ЕИС", false],
        ["Сбербанк-АСТ", false],
        ["Госплан", false],
        ["Тендерплан", true],
      ].map(([name, isNew]) => (
        <div
          key={String(name)}
          className={`flex items-center justify-between rounded-lg px-2.5 py-1.5 ${isNew ? "bg-sky-500/15 ring-1 ring-sky-400/40" : "bg-white/[0.05]"}`}
        >
          <span className={isNew ? "text-sky-100" : "text-zinc-400"}>{name}</span>
          {isNew ? <span className="text-[9.5px] text-sky-300">новый</span> : <span className="h-1.5 w-1.5 rounded-full bg-emerald-400/70" />}
        </div>
      ))}
    </div>
  ),
  "no-auto-analysis": () => (
    <div className="w-full max-w-[180px] space-y-1.5 text-[10.5px]">
      {["Документы", "Карточка площадки"].map((label) => (
        <div key={label} className="flex items-center gap-2 rounded-lg bg-white/[0.05] px-2.5 py-1.5 text-zinc-300">
          <Check size={11} className="text-emerald-300" /> {label}
        </div>
      ))}
      <div className="rounded-full border border-dashed border-white/20 py-1.5 text-center text-zinc-400">Разобрать — по кнопке</div>
    </div>
  ),
  "job-queue": () => (
    <div className="w-full max-w-[190px] space-y-1.5 text-left text-[10.5px]">
      {[
        ["0373100…41", 70],
        ["3245000…07", 25],
      ].map(([id, progress]) => (
        <div key={String(id)} className="rounded-lg bg-white/[0.05] px-2.5 py-1.5">
          <div className="flex items-center justify-between">
            <span className="font-mono text-zinc-300">{id}</span>
            <span className="text-[9.5px] text-rose-300">Отменить</span>
          </div>
          <span className="mt-1 block h-1 rounded-full bg-white/10">
            <span className="block h-full rounded-full bg-amber-400" style={{ width: `${progress}%` }} />
          </span>
        </div>
      ))}
    </div>
  ),
  "meter-vpu": () => (
    <div className="flex flex-col items-center gap-2 text-[11px]">
      <span className="rounded-full bg-white/[0.06] px-3 py-1 text-zinc-500 line-through decoration-rose-400/70">Счётчик ЭЭ</span>
      <span className="rounded-full bg-amber-500/15 px-3 py-1 text-amber-200 ring-1 ring-amber-400/40">ВПУ · 6–10 кВ</span>
    </div>
  ),
  "deepseek-fast": () => (
    <div className="flex flex-col items-center gap-2">
      <div className="flex items-center gap-2 tabular-nums">
        <span className="text-sm text-zinc-500 line-through">18 мин</span>
        <ArrowRight size={13} className="text-zinc-600" />
        <span className="text-2xl font-semibold text-sky-200">3,5 мин</span>
      </div>
      <span className="flex items-center gap-1.5 text-[10.5px] text-zinc-500">
        <AiProviderIcon provider="deepseek" size={14} /> полный разбор <Zap size={11} className="text-sky-300" />
      </span>
    </div>
  ),
  "catalog-hide-eol": () => (
    <div className="w-full max-w-[180px] space-y-2 text-[10.5px]">
      <div className="flex rounded-full bg-white/[0.06] p-0.5">
        <span className="flex-1 rounded-full bg-rose-500/25 py-1 text-center text-rose-100">В выпуске</span>
        <span className="flex-1 py-1 text-center text-zinc-500">Все</span>
      </div>
      {[
        ["МИРТЕК-12-РУ", true],
        ["МИРТЕК-1-РУ", false],
      ].map(([name, active]) => (
        <div key={String(name)} className={`flex items-center justify-between rounded-lg bg-white/[0.05] px-2.5 py-1.5 ${active ? "text-zinc-200" : "text-zinc-600"}`}>
          <span className={active ? "" : "line-through"}>{name}</span>
          {!active && <span className="text-[9px]">снят</span>}
        </div>
      ))}
    </div>
  ),
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
  qwen: () => (
    <div className="grid grid-cols-3 gap-1.5 text-[10.5px]">
      {(["yandex", "deepseek", "qwen"] as const).map((provider) => (
        <span
          key={provider}
          className={`flex items-center justify-center rounded-xl p-2 ${provider === "qwen" ? "bg-violet-500/15 ring-1 ring-violet-400/40" : "bg-white/[0.05] opacity-60"}`}
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
