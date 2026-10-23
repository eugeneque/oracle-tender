import { motion } from "motion/react";
import type { Variants } from "motion/react";
import { useCallback, useEffect, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { ArrowRight, Bell, Check, Clock3, Plus, Search, Send, ShieldCheck, Star, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { Link } from "react-router-dom";

import { AiProviderIcon } from "../AiProviderIcon";
import { EASE_OUT, staggerContainer } from "../../utils/motion";
import type { Slide } from "../../content/homeSlides";

// Итоговый экран в духе Apple-кейноута: мозаика плиток разного размера вокруг яркой
// центральной плитки. На плитке — короткое имя и мини-иллюстрация нововведения; полный
// текст открывается по нажатию. Раскладка задаётся `grid-template-areas` (класс `.bento` в
// index.css): имя области = `id` плитки, центральная — `hero`. С 24.10.2026 центральная плитка
// необязательна: у нововведений её место занял блок Миры над мозаикой (MiraSpotlight.tsx).

// ——— Мини-иллюстрации плиток ————————————————————————————————————————————————
// Редизайн 24.10.2026 под новый облик: монохром на графите и один акцент — фирменный синий
// (`indigo`). Цветные точки — только там, где цвет и есть смысл (работает / ошибка).

function Pill({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full bg-white/[0.06] px-2.5 py-1 text-[11px] text-zinc-300 ${className}`}>
      {children}
    </span>
  );
}

/** Строка-плашка мини-иллюстрации: подпись слева, значение справа. */
function Row({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`flex items-center gap-2 rounded-lg bg-white/[0.05] px-2.5 py-1.5 text-[10.5px] ${className}`}>{children}</div>
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
              n === 2 ? "bg-indigo-500 text-snow shadow-[0_0_24px_rgba(24,93,241,0.6)]" : "border border-white/15 text-zinc-400"
            }`}
          >
            {n}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Шкала «шаг N из M» — как у хода разбора в карточке закупки. */
function StepBar({ step, total }: { step: number; total: number }) {
  return (
    <span className="flex gap-1">
      {Array.from({ length: total }, (_, n) => (
        <span key={n} className={`h-1 flex-1 rounded-full ${n < step ? "bg-indigo-400" : "bg-white/10"}`} />
      ))}
    </span>
  );
}

const VISUALS: Record<string, () => ReactNode> = {
  "tender-list": () => (
    <div className="w-full max-w-[400px] space-y-2 text-left">
      {[
        { title: "Поставка счётчиков электроэнергии", meta: "ПАО «Россети Урал» · 14,2 млн ₽", step: 2, active: true },
        { title: "Приборы учёта АСКУЭ, 1 200 шт.", meta: "АО «Энергосбыт Плюс» · 6,8 млн ₽", step: 3, active: false },
      ].map((card) => (
        <div
          key={card.title}
          className={`rounded-xl px-3 py-2.5 ${
            card.active
              ? "bg-white/[0.06] shadow-[inset_0_0_0_1px_rgb(var(--c-indigo-400)/0.45),0_10px_24px_-14px_rgb(var(--c-indigo-500)/0.7)]"
              : "bg-white/[0.03] shadow-[inset_0_0_0_1px_rgb(var(--c-white)/0.07)]"
          }`}
        >
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-[11.5px] font-medium text-zinc-100">{card.title}</span>
            {card.active ? (
              <span className="shrink-0 text-[10px] tabular-nums text-indigo-300">Шаг 2 из 3</span>
            ) : (
              <Check size={12} className="shrink-0 text-zinc-400" />
            )}
          </div>
          <div className="mt-1 truncate text-[10px] text-zinc-500">{card.meta}</div>
          <div className="mt-2">
            <StepBar step={card.step} total={3} />
          </div>
        </div>
      ))}
    </div>
  ),
  sidebar: () => (
    <div className="flex w-full max-w-[200px] flex-col gap-1.5 text-left text-[10.5px]">
      <div className="flex items-center gap-2 rounded-lg bg-white/[0.05] px-2.5 py-1.5 text-zinc-500">
        <Search size={11} />
        <span className="flex-1">Поиск</span>
        <kbd className="rounded bg-white/[0.08] px-1.5 py-0.5 font-sans text-[9.5px] text-zinc-300">⌘K</kbd>
      </div>
      <span className="px-2.5 pt-1 text-[9px] uppercase tracking-wider text-zinc-600">Закупки</span>
      {["Тендеры", "Релевантные", "Аналитика"].map((label, n) => (
        <span
          key={label}
          className={`rounded-lg px-2.5 py-1.5 ${
            n === 0 ? "bg-white/[0.08] text-zinc-50 shadow-[inset_0_1px_0_rgb(var(--c-white)/0.08)]" : "text-zinc-400"
          }`}
        >
          {label}
        </span>
      ))}
    </div>
  ),
  palette: () => (
    <div className="flex flex-col items-center gap-3">
      <div className="flex -space-x-2.5">
        {[
          ["bg-[#185DF1]", "z-30"],
          ["bg-[#2a2d33]", "z-20"],
          ["bg-[#eef3ff]", "z-10"],
        ].map(([color, z]) => (
          <span key={color} className={`h-11 w-11 rounded-full shadow-[inset_0_0_0_1px_rgb(0_0_0/0.08)] ring-2 ring-zinc-950 ${color} ${z}`} />
        ))}
      </div>
      <span className="text-[10px] tabular-nums text-zinc-500">#185DF1 · графит · светлая</span>
    </div>
  ),
  "bitrix-deal": () => (
    <div className="flex w-full max-w-[420px] items-center gap-3 text-left">
      <div className="flex-1 space-y-2 rounded-xl bg-white/[0.04] p-2.5 shadow-[inset_0_0_0_1px_rgb(var(--c-white)/0.07)]">
        <span className="block truncate text-[11px] font-medium text-zinc-100">Поставка ПУ, 1 200 шт.</span>
        <span className="flex items-center justify-center gap-1.5 rounded-full bg-indigo-500 px-2.5 py-1 text-[10.5px] font-medium text-snow">
          <Send size={10} /> Отправить в Битрикс
        </span>
      </div>
      <ArrowRight size={14} className="shrink-0 text-zinc-600" />
      <div className="hidden flex-[1.3] space-y-1 sm:block">
        <span className="block text-[9.5px] uppercase tracking-wider text-zinc-500">ИИ анализ и решения</span>
        <Row>
          <span className="flex-1 truncate text-zinc-200">Краткая выжимка по ТЗ</span>
        </Row>
        <Row>
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-400" />
          <span className="flex-1 truncate text-zinc-300">Не проходит</span>
          <span className="tabular-nums text-zinc-500">2</span>
        </Row>
        <Row>
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-zinc-500" />
          <span className="flex-1 truncate text-zinc-300">Критичные</span>
          <span className="tabular-nums text-zinc-500">5</span>
        </Row>
      </div>
    </div>
  ),
  apps: () => (
    <div className="w-full max-w-[200px] space-y-1.5 text-left">
      {[
        ["B24", "Bitrix24", true],
        ["@", "Почта", true],
        ["R", "Rusprofile", false],
      ].map(([mark, label, on]) => (
        <Row key={String(label)}>
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-white/[0.08] text-[8.5px] font-semibold text-zinc-200">
            {mark}
          </span>
          <span className="min-w-0 flex-1 truncate text-zinc-200">{label}</span>
          <span className={`flex h-3.5 w-6 items-center rounded-full p-0.5 ${on ? "justify-end bg-indigo-500" : "bg-white/15"}`}>
            <span className="h-2.5 w-2.5 rounded-full bg-white" />
          </span>
        </Row>
      ))}
    </div>
  ),
  "ai-models": () => (
    <div className="w-full max-w-[200px] space-y-2.5">
      <div className="flex h-14 items-end gap-1">
        {[35, 55, 30, 80, 45, 100, 60].map((h, n) => (
          <span
            key={n}
            className={`flex-1 rounded-t-[4px] ${n === 5 ? "bg-indigo-400" : "bg-white/[0.1]"}`}
            style={{ height: `${h}%` }}
          />
        ))}
      </div>
      <div className="flex justify-between text-[10px] text-zinc-500">
        <span>прогноз на месяц</span>
        <span className="tabular-nums text-zinc-300">4,1 млн</span>
      </div>
    </div>
  ),
  qwen: () => (
    <div className="flex flex-col items-center gap-2.5">
      <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-white/[0.06] shadow-[inset_0_0_0_1px_rgb(var(--c-white)/0.08)]">
        <AiProviderIcon provider="qwen" size={30} />
      </span>
      <span className="text-[11px] font-medium text-zinc-200">Qwen3.8 27B</span>
      <span className="text-[10px] text-zinc-600 line-through">Claude · GigaChat</span>
    </div>
  ),
  positions: () => (
    <div className="w-full max-w-[210px] space-y-1 text-left text-[10.5px]">
      {[
        ["1", "Однофазный ПУ", "800"],
        ["2", "Трёхфазный ПУ", "400"],
      ].map(([n, label, qty]) => (
        <Row key={n}>
          <span className="w-3 text-zinc-500">{n}</span>
          <span className="min-w-0 flex-1 truncate text-zinc-200">{label}</span>
          <span className="tabular-nums text-zinc-400">{qty} шт.</span>
        </Row>
      ))}
      <div className="flex items-center justify-between border-t border-white/[0.08] px-2.5 pt-1.5 font-medium">
        <span className="text-zinc-300">Итого</span>
        <span className="tabular-nums text-indigo-300">1 200 шт.</span>
      </div>
    </div>
  ),
  verdicts: () => (
    <div className="w-full max-w-[210px] space-y-2 text-left">
      <Row>
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-400" />
        <span className="min-w-0 flex-1 truncate text-zinc-200">Межповерочный интервал 16 лет</span>
      </Row>
      <div className="flex gap-1.5">
        <span className="flex flex-1 items-center justify-center gap-1 rounded-full bg-white/[0.05] py-1 text-[10.5px] text-zinc-400">
          <ThumbsUp size={10} /> Согласен
        </span>
        <span className="flex flex-1 items-center justify-center gap-1 rounded-full bg-indigo-500/15 py-1 text-[10.5px] text-indigo-200 ring-1 ring-indigo-400/40">
          <ThumbsDown size={10} /> Не согласен
        </span>
      </div>
      <span className="block text-center text-[10px] text-zinc-500">учитывается в похожих закупках</span>
    </div>
  ),
  matrix: () => (
    <div className="w-full max-w-[420px] space-y-1 text-left">
      {[
        ["Напряжение 3×380 В", "= 3×230/400 В"],
        ["Срок службы 192 месяца", "= 16 лет"],
        ["Класс точности 1/2", "сверено без модели"],
      ].map(([req, note]) => (
        <Row key={req}>
          <Check size={11} className="shrink-0 text-indigo-300" />
          <span className="min-w-0 flex-1 truncate text-zinc-200">{req}</span>
          <span className="shrink-0 text-[10px] text-zinc-500">{note}</span>
        </Row>
      ))}
    </div>
  ),
  pp1875: () => (
    <div className="flex flex-col items-center gap-2.5">
      <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/[0.06] text-zinc-100">
        <ShieldCheck size={24} />
      </span>
      <Pill>
        <span className="h-1.5 w-1.5 rounded-full bg-indigo-400" /> реестр российской продукции
      </Pill>
    </div>
  ),
  activity: () => (
    <div className="flex w-full max-w-[400px] items-start gap-3 text-left">
      <span className="relative mt-1 hidden h-10 w-10 shrink-0 items-center justify-center rounded-full bg-white/[0.06] text-zinc-200 sm:flex">
        <Bell size={17} />
        <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-indigo-400 ring-2 ring-zinc-950" />
      </span>
      <div className="flex-1 space-y-1.5">
        {[
          ["Разбор закупки", "Шаг 2 из 3", 2],
          ["Опрос площадок", "7 из 12", 7],
        ].map(([label, note, done]) => (
          <div key={String(label)} className="space-y-1.5 rounded-lg bg-white/[0.05] px-2.5 py-2 text-[10.5px]">
            <div className="flex justify-between">
              <span className="text-zinc-200">{label}</span>
              <span className="tabular-nums text-zinc-500">{note}</span>
            </div>
            <span className="block h-1 overflow-hidden rounded-full bg-white/10">
              <span
                className="block h-full rounded-full bg-indigo-400"
                style={{ width: `${(Number(done) / (label === "Опрос площадок" ? 12 : 3)) * 100}%` }}
              />
            </span>
          </div>
        ))}
      </div>
    </div>
  ),
  "catalog-sources": () => (
    <div className="flex w-full max-w-[420px] gap-2.5 text-left text-[10.5px]">
      <div className="flex-1 space-y-1">
        {[
          ["Ток макс.", "80 А", false],
          ["Класс точности", "1/1", false],
          ["Степень защиты", "IP54", true],
        ].map(([label, value, unchecked]) => (
          <Row key={String(label)}>
            <span className="min-w-0 flex-1 truncate text-zinc-500">{label}</span>
            <span className="flex items-center gap-1 text-zinc-100">
              {value}
              {unchecked && <span className="h-1.5 w-1.5 rounded-full bg-amber-400" />}
            </span>
          </Row>
        ))}
      </div>
      <div className="hidden w-[44%] flex-col gap-1.5 rounded-xl bg-white/[0.03] p-2.5 shadow-[inset_0_0_0_1px_rgb(var(--c-white)/0.08)] sm:flex">
        <span className="text-[10px] font-medium text-zinc-300">Источники данных</span>
        <span className="flex items-center gap-1.5 text-zinc-400"><Check size={10} className="text-indigo-300" />Сайт производителя</span>
        <span className="flex items-center gap-1.5 text-zinc-400"><Check size={10} className="text-indigo-300" />Описание типа</span>
        <span className="flex items-center gap-1.5 text-zinc-500"><X size={10} />Руководство — PDF</span>
      </div>
    </div>
  ),
  deadline: () => (
    <div className="flex flex-col items-center gap-1.5">
      <span className="text-[10px] uppercase tracking-wider text-zinc-500">окончание подачи</span>
      <span className="text-2xl font-semibold tabular-nums tracking-tight text-zinc-50">
        28 окт · <span className="text-indigo-300">09:00</span>
      </span>
      <span className="text-[10px] text-zinc-500">по времени заказчика · UTC+5</span>
    </div>
  ),
  search: () => (
    <div className="w-full max-w-[200px] space-y-2 text-left">
      <div className="flex items-center gap-2 rounded-full bg-white/[0.07] px-3 py-1.5 text-[11px] text-zinc-200 ring-1 ring-indigo-400/40">
        <Search size={12} className="text-indigo-300" />
        счетчик
      </div>
      {["Поставка счётчиков ЭЭ", "Счётчики АСКУЭ, монтаж"].map((title) => (
        <div key={title} className="truncate rounded-lg bg-white/[0.05] px-2.5 py-1.5 text-[10.5px] text-zinc-400">
          {title.split(/(Счётчик|счётчик)/).map((part, n) =>
            /ётчик/.test(part) ? <mark key={n} className="rounded-sm bg-indigo-400/25 text-zinc-50">{part}</mark> : part,
          )}
        </div>
      ))}
    </div>
  ),
  roseltorg: () => (
    <div className="flex w-full max-w-[210px] flex-col items-center gap-2.5">
      <div className="flex flex-wrap justify-center gap-1.5">
        {["Россети", "РусГидро", "Ростелеком", "Интер РАО"].map((name) => (
          <Pill key={name}>{name}</Pill>
        ))}
      </div>
      <span className="flex items-center gap-1.5 text-[10px] tabular-nums text-zinc-500">
        <Clock3 size={11} /> опрос в 06:00 и 20:00 МСК
      </span>
    </div>
  ),
  sberbank: () => (
    <div className="relative w-full max-w-[200px] space-y-2.5 pl-4 text-left text-[10.5px]">
      <span className="absolute bottom-1.5 left-[3px] top-1.5 w-px bg-white/10" />
      {[
        ["Запрос разъяснений", "21 окт"],
        ["Ответ заказчика", "22 окт"],
        ["Протокол рассмотрения", "сегодня"],
      ].map(([label, date], n) => (
        <div key={label} className="relative flex items-center gap-2">
          <span className={`absolute -left-4 h-[7px] w-[7px] rounded-full ${n === 2 ? "bg-indigo-400" : "bg-zinc-600"}`} />
          <span className="min-w-0 flex-1 truncate text-zinc-200">{label}</span>
          <span className="shrink-0 text-[10px] text-zinc-500">{date}</span>
        </div>
      ))}
    </div>
  ),
  selection: () => (
    <div className="w-full max-w-[400px] space-y-1.5 text-left text-[10.5px]">
      {[
        ["Собрано", 1240, 100],
        ["Профили", 310, 46],
        ["Модель", 96, 24],
        ["Фильтры = в списке", 42, 12],
      ].map(([label, count, width], n, all) => (
        <div key={String(label)} className="flex items-center gap-2.5">
          <span className="w-[42%] shrink-0 truncate text-zinc-400">{label}</span>
          <span className="flex-1">
            <span
              className={`block h-2 rounded-full ${n === all.length - 1 ? "bg-indigo-400" : "bg-white/[0.12]"}`}
              style={{ width: `${width}%` }}
            />
          </span>
          <span className="w-9 shrink-0 text-right tabular-nums text-zinc-300">{Number(count).toLocaleString("ru-RU")}</span>
        </div>
      ))}
    </div>
  ),
  // ——— Гайд «С чего начать» ———
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

export function SlideDetails({ slide, onClose }: { slide: Slide; onClose: () => void }) {
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
        <div className="mb-5 flex h-11 w-11 items-center justify-center rounded-2xl bg-white/[0.06] text-zinc-100">
          <Icon size={21} />
        </div>
        <div className="text-xs font-medium text-zinc-500">{slide.kicker}</div>
        <h2 className="mt-1.5 text-2xl font-semibold tracking-tight text-zinc-50">{slide.title}</h2>
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

// Плитка в новом стиле (24.10.2026): поверхность `.surface`, как у блоков на страницах, текст
// слева — имя и дата, под ними одна фраза, ниже мини-иллюстрация. Без цветной заливки и
// цветных значков: на тёмном графите акцент — только синий в самой иллюстрации.
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
      className="bento-tile bento-card surface group relative flex min-h-[200px] flex-col overflow-hidden rounded-[22px] p-5 text-left"
    >
      <span className="flex w-full items-baseline justify-between gap-3">
        <span className="text-[15px] font-semibold leading-snug tracking-tight text-zinc-100">{slide.tile}</span>
        <span className="shrink-0 text-[11px] text-zinc-500">{slide.kicker}</span>
      </span>
      <span className="mt-1 line-clamp-2 text-[12.5px] leading-snug text-zinc-500">{slide.lead}</span>
      <span className="flex w-full flex-1 items-center justify-center pb-1 pt-4">
        {Visual ? (
          <Visual />
        ) : (
          <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-white/[0.06] text-zinc-200">
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
  /** Центральная плитка с градиентом (гайд); без неё мозаика — только из плиток. */
  hero?: { title: string; subtitle: string };
  /** Что открывается по нажатию на центральную плитку; без него она не нажимается. */
  heroSlide?: Slide;
  slides: Slide[];
  /** `grid-template-areas` на широком экране: четыре колонки, область центральной плитки — `hero`. */
  areas: string[];
}) {
  const [openSlide, setOpenSlide] = useState<Slide | null>(null);
  const closeDetails = useCallback(() => setOpenSlide(null), []);

  const heroContent = hero && (
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
        {hero &&
          (heroSlide ? (
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
          ))}
        {slides.map((slide) => (
          <Tile key={slide.id} slide={slide} onOpen={setOpenSlide} />
        ))}
      </motion.div>
      {openSlide && <SlideDetails slide={openSlide} onClose={closeDetails} />}
    </>
  );
}
