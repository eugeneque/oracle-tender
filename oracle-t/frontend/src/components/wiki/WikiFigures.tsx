import {
  ArrowRight,
  BarChart3,
  Bell,
  Boxes,
  Building2,
  Check,
  Cloud,
  Database,
  FileText,
  Filter,
  Globe,
  KanbanSquare,
  KeyRound,
  LayoutGrid,
  Lock,
  Minus,
  ScrollText,
  Search,
  Server,
  Settings,
  Sparkles,
  Star,
  Timer,
  X,
} from "lucide-react";
import type { ReactNode } from "react";

import type { WikiFigure } from "../../content/wiki";

/**
 * Иллюстрации Сова Wiki (19.11.2026). Это не скриншоты, а схематичные макеты интерфейса из
 * тех же классов Tailwind: они меняются вместе с темой и не устаревают с каждой правкой
 * вёрстки, как устаревали бы картинки. Каждая показывает одну мысль статьи — не весь экран.
 */

function Window({ title, children, className = "" }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <div className={`overflow-hidden rounded-2xl border border-white/10 bg-zinc-900 ${className}`}>
      <div className="flex h-8 items-center gap-1.5 border-b border-white/[0.06] bg-white/[0.02] px-3">
        <span className="h-2 w-2 rounded-full bg-zinc-700" />
        <span className="h-2 w-2 rounded-full bg-zinc-700" />
        <span className="h-2 w-2 rounded-full bg-zinc-700" />
        {title && <span className="ml-2 truncate text-[11px] text-zinc-500">{title}</span>}
      </div>
      {children}
    </div>
  );
}

function Pill({ children, tone = "zinc" }: { children: ReactNode; tone?: "zinc" | "indigo" | "emerald" | "amber" | "red" | "sky" }) {
  const tones = {
    zinc: "border-white/10 bg-white/[0.04] text-zinc-400",
    indigo: "border-indigo-400/30 bg-indigo-500/10 text-indigo-300",
    emerald: "border-emerald-400/30 bg-emerald-500/10 text-emerald-300",
    amber: "border-amber-400/30 bg-amber-500/10 text-amber-300",
    red: "border-red-400/30 bg-red-500/10 text-red-300",
    sky: "border-sky-400/30 bg-sky-500/10 text-sky-300",
  };
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10.5px] font-medium ${tones[tone]}`}>
      {children}
    </span>
  );
}

function Bar({ value, tone = "bg-indigo-500" }: { value: number; tone?: string }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]">
      <div className={`h-full rounded-full ${tone}`} style={{ width: `${value}%` }} />
    </div>
  );
}

function Callout({ n, children }: { n: number; children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-zinc-400">
      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-indigo-500 text-[9px] font-semibold text-snow">
        {n}
      </span>
      {children}
    </span>
  );
}

/* ---------------------------------------------------------------- Конвейер */

function Pipeline() {
  const steps: { icon: ReactNode; title: string; hint: string; auto: boolean }[] = [
    { icon: <Globe size={16} />, title: "Площадки", hint: "ЕИС, Росэлторг, Сбербанк-АСТ…", auto: true },
    { icon: <Filter size={16} />, title: "Отбор", hint: "Профили + ИИ", auto: true },
    { icon: <FileText size={16} />, title: "Разбор", hint: "Требования из документов", auto: true },
    { icon: <Boxes size={16} />, title: "Матрица", hint: "Требования × каталог", auto: true },
    { icon: <Sparkles size={16} />, title: "Оценка", hint: "Вердикт и % победителя", auto: true },
    { icon: <Check size={16} />, title: "Решение", hint: "Идти / не идти", auto: false },
  ];
  return (
    <div className="grid grid-cols-2 gap-2 p-4 sm:grid-cols-6">
      {steps.map((s, i) => (
        <div key={s.title} className="relative">
          <div
            className={`flex h-full flex-col gap-2 rounded-xl border p-3 ${
              s.auto ? "border-white/10 bg-white/[0.03]" : "border-indigo-400/40 bg-indigo-500/10"
            }`}
          >
            <span className={`flex h-7 w-7 items-center justify-center rounded-lg ${s.auto ? "bg-white/[0.06] text-zinc-300" : "bg-indigo-500 text-snow"}`}>
              {s.icon}
            </span>
            <div className="text-xs font-semibold text-zinc-100">{s.title}</div>
            <div className="text-[11px] leading-snug text-zinc-500">{s.hint}</div>
            <div className="mt-auto text-[10px] uppercase tracking-wide text-zinc-600">{s.auto ? "автоматически" : "человек"}</div>
          </div>
          {i < steps.length - 1 && (
            <ArrowRight size={14} className="absolute -right-2.5 top-1/2 z-10 hidden -translate-y-1/2 text-zinc-600 sm:block" />
          )}
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------- Боковая панель */

function Sidebar() {
  const groups: { label: string; items: { icon: ReactNode; text: string; active?: boolean; sub?: boolean }[] }[] = [
    {
      label: "Тендеры",
      items: [
        { icon: <KanbanSquare size={14} />, text: "Тендеры", active: true },
        { icon: <Star size={13} />, text: "Избранное", sub: true },
        { icon: <Timer size={13} />, text: "Минутки", sub: true },
      ],
    },
    {
      label: "Рынок",
      items: [
        { icon: <BarChart3 size={14} />, text: "Аналитика" },
        { icon: <Boxes size={14} />, text: "Каталог продукции" },
      ],
    },
    { label: "Компания", items: [{ icon: <Building2 size={14} />, text: "Моя компания" }] },
    {
      label: "Система",
      items: [
        { icon: <Sparkles size={14} />, text: "Модели ИИ" },
        { icon: <LayoutGrid size={14} />, text: "Приложения" },
        { icon: <ScrollText size={14} />, text: "Логирование" },
        { icon: <Settings size={14} />, text: "Настройки" },
      ],
    },
  ];
  return (
    <div className="grid gap-4 p-4 sm:grid-cols-[200px_1fr]">
      <div className="rounded-xl border border-white/10 bg-white/[0.02] p-2.5">
        <div className="mb-2 flex items-center gap-1.5 rounded-lg bg-white/[0.04] px-2 py-1.5 text-[11px] text-zinc-500">
          <Search size={12} /> Поиск <span className="ml-auto rounded bg-white/[0.06] px-1 text-[10px]">⌘K</span>
        </div>
        {groups.map((g) => (
          <div key={g.label} className="mb-2">
            <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-zinc-600">{g.label}</div>
            {g.items.map((it) => (
              <div
                key={it.text}
                className={`flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs ${it.sub ? "ml-4" : ""} ${
                  it.active ? "bg-indigo-500/15 text-indigo-200" : "text-zinc-400"
                }`}
              >
                {it.icon}
                {it.text}
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="flex flex-col justify-center gap-3 text-xs text-zinc-400">
        <Callout n={1}>Группы разделов; активный пункт — на плашке</Callout>
        <Callout n={2}>⌘K — быстрый переход в любой раздел и поиск по закупкам</Callout>
        <Callout n={3}>Нажатие на знак совы сворачивает панель в колонку значков</Callout>
        <Callout n={4}>Аватар внизу — меню аккаунта, «Что нового?» и Wiki</Callout>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- Роли */

function Roles() {
  const rows = [
    ["Тендеры", true, true, true],
    ["Аналитика", false, true, true],
    ["Каталог", true, false, true],
    ["Моя компания", true, false, true],
    ["Приложения", true, false, true],
    ["Настройки · Логи", true, true, true],
    ["Модели ИИ · Обновление · Пользователи", false, false, true],
  ] as const;
  const Cell = ({ ok }: { ok: boolean }) =>
    ok ? <Check size={15} className="mx-auto text-emerald-400" /> : <Minus size={15} className="mx-auto text-zinc-700" />;
  return (
    <div className="overflow-x-auto p-4">
      <table className="w-full min-w-[460px] text-xs">
        <thead>
          <tr className="text-zinc-500">
            <th className="pb-2 text-left font-medium">Раздел</th>
            <th className="pb-2 font-medium">Пользователь</th>
            <th className="pb-2 font-medium">Аналитик</th>
            <th className="pb-2 font-medium">Администратор</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, u, a, ad]) => (
            <tr key={name} className="border-t border-white/[0.06]">
              <td className="py-2 text-zinc-300">{name}</td>
              <td><Cell ok={u} /></td>
              <td><Cell ok={a} /></td>
              <td><Cell ok={ad} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* -------------------------------------------------------- Список закупок */

function TenderList() {
  const items = [
    { t: "Поставка приборов учёта электроэнергии", c: "АО «Энергосбыт Плюс»", d: "ok", s: 78, left: "5 дн.", active: true },
    { t: "Выполнение работ по замене ИПУ", c: "ПАО «Россети Центр»", d: "no", s: 31, left: "12 дн." },
    { t: "Поставка АСКУЭ для многоквартирных домов", c: "МУП «Горэлектросеть»", d: "none", s: 54, left: "2 дн." },
  ];
  return (
    <div className="grid gap-px bg-white/[0.06] sm:grid-cols-[1.1fr_1fr]">
      <div className="space-y-2 bg-zinc-900 p-3">
        <div className="flex items-center gap-2 rounded-lg bg-white/[0.04] px-2.5 py-1.5 text-[11px] text-zinc-500">
          <Search size={12} /> счётчик электроэнергии
          <span className="ml-auto text-zinc-600">Показано 53 из 17 561</span>
        </div>
        {items.map((it) => (
          <div key={it.t} className={`rounded-xl border p-2.5 ${it.active ? "border-indigo-400/40 bg-indigo-500/[0.07]" : "border-white/10"}`}>
            <div className="flex items-start gap-2">
              <span
                className={`mt-1 h-2 w-2 shrink-0 rounded-full ${it.d === "ok" ? "bg-emerald-400" : it.d === "no" ? "bg-red-400" : "bg-zinc-600"}`}
              />
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium text-zinc-200">{it.t}</div>
                <div className="mt-0.5 truncate text-[10.5px] text-zinc-500">{it.c} · {it.left}</div>
              </div>
              <span className="text-xs font-medium tabular-nums text-zinc-400">{it.s}%</span>
            </div>
          </div>
        ))}
      </div>
      <div className="bg-zinc-900 p-3">
        <div className="flex items-center gap-2">
          <Pill tone="indigo">На проверке</Pill>
          <Pill>Россети</Pill>
          <Star size={13} className="ml-auto text-amber-400" />
        </div>
        <div className="mt-2 text-sm font-semibold text-zinc-100">Поставка приборов учёта электроэнергии</div>
        <div className="mt-2 flex gap-3 text-[11px] text-zinc-500">
          <span className="border-b border-indigo-400 pb-1 text-zinc-200">Основное</span>
          <span>Документы</span>
          <span>Требования</span>
          <span>Конкуренция</span>
        </div>
        <div className="mt-3 space-y-1.5">
          <Bar value={78} />
          <Bar value={55} tone="bg-sky-500" />
          <Bar value={32} tone="bg-amber-500" />
        </div>
        <div className="mt-3 rounded-lg bg-white/[0.04] p-2 text-[11px] leading-relaxed text-zinc-400">
          Заключение ИИ: подходят модели МИРТЕК серии …
        </div>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- Kanban */

function Kanban() {
  const cols: { t: string; tone: string; cards: number }[] = [
    { t: "AI отобрал", tone: "bg-indigo-400", cards: 3 },
    { t: "На проверке", tone: "bg-amber-400", cards: 2 },
    { t: "Заявка подана", tone: "bg-sky-400", cards: 1 },
    { t: "Выиграли", tone: "bg-emerald-400", cards: 1 },
    { t: "Проиграли", tone: "bg-zinc-500", cards: 1 },
    { t: "Отклонён", tone: "bg-red-400", cards: 1 },
  ];
  return (
    <div className="grid grid-cols-3 gap-2 p-3 sm:grid-cols-6">
      {cols.map((c) => (
        <div key={c.t} className="rounded-xl bg-white/[0.03] p-2">
          <div className="mb-2 flex items-center gap-1.5 text-[11px] font-medium text-zinc-300">
            <span className={`h-1.5 w-1.5 rounded-full ${c.tone}`} />
            <span className="truncate">{c.t}</span>
            <span className="ml-auto text-zinc-600">{c.cards}</span>
          </div>
          {Array.from({ length: c.cards }).map((_, i) => (
            <div key={i} className="mb-1.5 rounded-lg border border-white/10 bg-zinc-900 p-2">
              <div className="h-1.5 w-4/5 rounded bg-white/15" />
              <div className="mt-1.5 h-1.5 w-1/2 rounded bg-white/[0.07]" />
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/* ----------------------------------------------------------------- Воронка */

function Funnel() {
  const layers = [
    { t: "Собрано", n: "17 561", w: 100, hint: "Все закупки с площадок" },
    { t: "Профили", n: "1 240", w: 74, hint: "Слова · исключения · ОКПД2" },
    { t: "Модель (ИИ)", n: "410", w: 52, hint: "Подтверждено моделью" },
    { t: "Фильтры = в списке", n: "53", w: 32, hint: "Ваши фильтры на странице" },
  ];
  return (
    <div className="space-y-2 p-4">
      {layers.map((l, i) => (
        <div key={l.t} className="grid grid-cols-[1fr_auto] items-center gap-3 sm:grid-cols-[170px_1fr_auto]">
          <div>
            <div className="text-xs font-semibold text-zinc-200">
              <span className="mr-1.5 text-zinc-600">{i + 1}</span>
              {l.t}
            </div>
            <div className="text-[10.5px] text-zinc-500">{l.hint}</div>
          </div>
          <div className="hidden h-7 sm:block">
            <div className="h-full rounded-md bg-gradient-to-r from-indigo-500/60 to-indigo-500/20" style={{ width: `${l.w}%` }} />
          </div>
          <div className="text-right text-lg font-semibold tabular-nums text-zinc-100">{l.n}</div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ Профиль */

function Profile() {
  const Words = ({ items, tone }: { items: string[]; tone: "indigo" | "red" | "sky" }) => (
    <div className="flex flex-wrap gap-1.5">
      {items.map((w) => (
        <Pill key={w} tone={tone}>
          {w}
        </Pill>
      ))}
    </div>
  );
  return (
    <div className="grid gap-px bg-white/[0.06] sm:grid-cols-[1.3fr_1fr]">
      <div className="space-y-3 bg-zinc-900 p-4">
        <div>
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">Ключевые слова</div>
          <Words tone="indigo" items={["счётчик", "прибор учёта", "АСКУЭ", "УСПД"]} />
        </div>
        <div>
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">Слова-исключения</div>
          <Words tone="red" items={["мебель", "канцелярия", "вода"]} />
        </div>
        <div>
          <div className="mb-1.5 text-[11px] font-medium uppercase tracking-wide text-zinc-500">ОКПД2</div>
          <Words tone="sky" items={["26.51.63", "26.51.6"]} />
        </div>
        <div className="flex gap-2 text-[11px] text-zinc-400">
          <Pill>слова: любое</Pill>
          <Pill>ОКПД2: достаточно одного</Pill>
          <Pill>площадки: все</Pill>
        </div>
      </div>
      <div className="bg-zinc-900 p-4">
        <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">Предпросмотр</div>
        <div className="mt-1 text-3xl font-semibold tabular-nums text-zinc-50">1 240</div>
        <div className="text-[11px] text-zinc-500">закупок найдёт профиль</div>
        <div className="mt-3 space-y-1.5">
          {["Поставка счётчиков СЭТ-4ТМ", "Приобретение ПУ 3ф", "АСКУЭ — модернизация"].map((t) => (
            <div key={t} className="truncate rounded-md bg-white/[0.04] px-2 py-1 text-[11px] text-zinc-400">{t}</div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- Этапы */

function Stages() {
  const st: { t: string; tone: "indigo" | "amber" | "sky" | "emerald" | "zinc" | "red" }[] = [
    { t: "AI отобрал", tone: "indigo" },
    { t: "На проверке", tone: "amber" },
    { t: "Заявка подана", tone: "sky" },
    { t: "Выиграли", tone: "emerald" },
  ];
  return (
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        {st.map((s, i) => (
          <span key={s.t} className="flex items-center gap-2">
            <Pill tone={s.tone}>{s.t}</Pill>
            {i < st.length - 1 && <ArrowRight size={13} className="text-zinc-600" />}
          </span>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-zinc-500">
        Ответвления: <Pill tone="zinc">Проиграли</Pill> <Pill tone="red">Отклонён</Pill>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        <div className="rounded-xl border border-white/10 p-3 text-[11px] text-zinc-400"><b className="text-zinc-200">Релевантна / Неактуально</b><br />Общие списки для всей команды</div>
        <div className="rounded-xl border border-white/10 p-3 text-[11px] text-zinc-400"><b className="text-zinc-200">Звёздочка</b><br />«Избранное» — только ваше</div>
        <div className="rounded-xl border border-white/10 p-3 text-[11px] text-zinc-400"><b className="text-zinc-200">Теги и комментарии</b><br />Общие, с историей изменений</div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------- Вкладки карточки */

function CardTabs() {
  const tabs = ["Основное", "Документы", "Дополнительно", "Требования", "Конкуренция", "Расчёт", "Протоколы", "Журнал событий", "Заявка", "История"];
  return (
    <div className="p-4">
      <div className="flex flex-wrap gap-x-4 gap-y-2 border-b border-white/[0.06] pb-2 text-xs text-zinc-500">
        {tabs.map((t, i) => (
          <span key={t} className={i === 0 ? "border-b-2 border-indigo-400 pb-2 text-zinc-100" : ""}>
            {t}
          </span>
        ))}
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        {[
          ["Основное", "Заключение ИИ, AI-оценка, НМЦК, позиции"],
          ["Требования", "Что просит заказчик — к товару, работам, участнику"],
          ["Конкуренция", "Матрица соответствия и процент победителя"],
        ].map(([t, d]) => (
          <div key={t} className="rounded-xl border border-white/10 p-3">
            <div className="text-xs font-semibold text-zinc-200">{t}</div>
            <div className="mt-0.5 text-[11px] leading-snug text-zinc-500">{d}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

/* --------------------------------------------------------------------- Мира */

function Blob({ from, to, mood, label }: { from: string; to: string; mood: "happy" | "squint" | "sad"; label: string }) {
  return (
    <div className="flex flex-col items-center gap-2">
      <div
        className="relative h-20 w-20 rounded-[46%_54%_52%_48%/55%_45%_55%_45%]"
        style={{ background: `radial-gradient(circle at 35% 30%, ${from}, ${to})`, boxShadow: `0 0 36px ${to}66` }}
      >
        <span className="absolute left-[28%] top-[40%] h-2.5 w-2 rounded-full bg-white/95" />
        <span className="absolute right-[28%] top-[40%] h-2.5 w-2 rounded-full bg-white/95" />
        <span
          className="absolute left-1/2 h-2 w-5 -translate-x-1/2 border-white/90"
          style={
            mood === "happy"
              ? { top: "58%", borderBottom: "2px solid", borderRadius: "0 0 12px 12px" }
              : mood === "sad"
                ? { top: "62%", borderTop: "2px solid", borderRadius: "12px 12px 0 0" }
                : { top: "62%", borderBottom: "2px solid" }
          }
        />
      </div>
      <span className="text-[11px] text-zinc-400">{label}</span>
    </div>
  );
}

function MiraFigure() {
  return (
    <div className="flex flex-col items-center gap-4 p-5 sm:flex-row sm:justify-around">
      <div className="max-w-[210px] text-center text-xs text-zinc-400 sm:text-left">
        <div className="mb-1 text-sm font-semibold text-zinc-100">Мира ведёт разбор</div>
        Шкала <b className="text-zinc-200">«1 из 3»</b> по контуру блока: документы → матрица → заключение.
      </div>
      <Blob from="#8ff0b8" to="#10b981" mood="happy" label="Подходим" />
      <Blob from="#fde08a" to="#f59e0b" mood="squint" label="Есть оговорки" />
      <Blob from="#fda4b8" to="#ef4444" mood="sad" label="Не проходим" />
    </div>
  );
}

/* ------------------------------------------------------------------ Матрица */

function Matrix() {
  const V = ({ v }: { v: "yes" | "no" | "nodata" }) =>
    v === "yes" ? (
      <Check size={14} className="text-emerald-400" />
    ) : v === "no" ? (
      <X size={14} className="text-red-400" />
    ) : (
      <Minus size={14} className="text-zinc-600" />
    );
  const rows: { r: string; k: string; a: "yes" | "no" | "nodata"; b: "yes" | "no" | "nodata"; c: "yes" | "no" | "nodata" }[] = [
    { r: "Класс точности 0,5S", k: "критично", a: "yes", b: "yes", c: "no" },
    { r: "Интерфейс RS-485", k: "важно", a: "yes", b: "nodata", c: "yes" },
    { r: "Срок службы ≥ 16 лет", k: "важно", a: "yes", b: "yes", c: "yes" },
    { r: "Запись в реестре ПП 719", k: "критично", a: "yes", b: "no", c: "yes" },
  ];
  return (
    <div className="overflow-x-auto p-4">
      <table className="w-full min-w-[460px] text-xs">
        <thead>
          <tr className="text-zinc-500">
            <th className="pb-2 text-left font-medium">Требование</th>
            <th className="pb-2 font-medium text-indigo-300">МИРТЕК</th>
            <th className="pb-2 font-medium">Конкурент А</th>
            <th className="pb-2 font-medium">Конкурент Б</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((x) => (
            <tr key={x.r} className="border-t border-white/[0.06]">
              <td className="py-2 text-zinc-300">
                {x.r} <span className="ml-1 text-[10px] text-zinc-600">{x.k}</span>
              </td>
              {[x.a, x.b, x.c].map((v, i) => (
                <td key={i} className="py-2"><span className="flex justify-center"><V v={v} /></span></td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-3 flex items-center gap-3 rounded-lg bg-white/[0.04] p-2.5 text-[11px] text-zinc-400">
        «Не проходит: реестр ПП 719 — записи нет».
        <span className="ml-auto flex gap-1.5">
          <Pill tone="emerald">Согласен</Pill>
          <Pill tone="amber">Не согласен</Pill>
        </span>
      </div>
    </div>
  );
}

/* ----------------------------------------------------------------- AI-оценка */

function AiScore() {
  const dims = [
    ["Задача", 82, "bg-emerald-500"],
    ["Компетенции", 64, "bg-sky-500"],
    ["История", 0, "bg-zinc-600"],
  ] as const;
  return (
    <div className="grid gap-4 p-4 sm:grid-cols-[1.2fr_1fr]">
      <div className="space-y-3">
        {dims.map(([n, v, tone]) => (
          <div key={n}>
            <div className="mb-1 flex justify-between text-xs">
              <span className="text-zinc-300">{n}</span>
              <span className="tabular-nums text-zinc-500">{v === 0 ? "недостаточно данных" : `${v}%`}</span>
            </div>
            <Bar value={v === 0 ? 0 : v} tone={tone} />
          </div>
        ))}
        <div className="text-[11px] text-zinc-500">Измерение без данных исключается из итога — оно не считается нулём.</div>
      </div>
      <div className="rounded-xl border border-white/10 p-3">
        <Pill tone="amber">ИДТИ С ОГОВОРКАМИ</Pill>
        <div className="mt-2 text-[11px] font-medium text-zinc-300">Слабые места</div>
        <ul className="mt-1 space-y-1 text-[11px] text-zinc-400">
          <li><span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-red-400" />нет записи в реестре ПП 719</li>
          <li><span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-amber-400" />короткий срок подачи</li>
          <li><span className="mr-1 inline-block h-1.5 w-1.5 rounded-full bg-zinc-500" />мало опыта у заказчика</li>
        </ul>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ Bitrix24 */

function BitrixFlow() {
  const Box = ({ icon, t, d }: { icon: ReactNode; t: string; d: string }) => (
    <div className="flex-1 rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-white/[0.06] text-zinc-300">{icon}</span>
      <div className="mt-2 text-xs font-semibold text-zinc-100">{t}</div>
      <div className="text-[11px] leading-snug text-zinc-500">{d}</div>
    </div>
  );
  return (
    <div className="flex flex-col items-stretch gap-2 p-4 sm:flex-row sm:items-center">
      <Box icon={<KanbanSquare size={15} />} t="Карточка в Сове" d="Этап «На проверке», разбор ИИ готов" />
      <ArrowRight size={16} className="mx-auto shrink-0 rotate-90 text-zinc-600 sm:rotate-0" />
      <Box icon={<Lock size={15} />} t="«Отправить в Битрикс»" d="Пакет подписан, строки с постоянными ключами" />
      <ArrowRight size={16} className="mx-auto shrink-0 rotate-90 text-zinc-600 sm:rotate-0" />
      <Box icon={<Cloud size={15} />} t="Сделка Bitrix24" d="Вкладка «ИИ анализ и решения»: ТЗ, договор, итоги" />
    </div>
  );
}

/* ----------------------------------------------------------------- Аналитика */

function Analytics() {
  const bars = [30, 44, 38, 56, 62, 49, 71, 66, 80, 74, 88, 93];
  return (
    <div className="grid gap-px bg-white/[0.06] sm:grid-cols-[1.4fr_1fr]">
      <div className="bg-zinc-900 p-4">
        <div className="mb-3 flex items-center justify-between">
          <span className="text-xs font-medium text-zinc-300">Закупки по месяцам</span>
          <span className="flex gap-1"><Pill tone="indigo">12 мес.</Pill><Pill>6</Pill><Pill>24</Pill></span>
        </div>
        <div className="flex h-28 items-end gap-1.5">
          {bars.map((h, i) => (
            <div key={i} className="flex-1 rounded-t bg-indigo-500/70" style={{ height: `${h}%` }} />
          ))}
        </div>
      </div>
      <div className="space-y-2 bg-zinc-900 p-4">
        <div className="text-xs font-medium text-zinc-300">Топ заказчиков</div>
        {[["Россети Центр", 92], ["Энергосбыт Плюс", 71], ["Горэлектросеть", 48]].map(([n, v]) => (
          <div key={n as string}>
            <div className="mb-0.5 flex justify-between text-[11px] text-zinc-500"><span>{n}</span><span>{v}</span></div>
            <Bar value={v as number} />
          </div>
        ))}
      </div>
    </div>
  );
}

function Market() {
  const rows: [string, number][] = [["Производитель 1", 92], ["Производитель 2", 74], ["Производитель 3", 58], ["Производитель 4", 31]];
  return (
    <div className="grid gap-px bg-white/[0.06] sm:grid-cols-[1.2fr_1fr]">
      <div className="space-y-2 bg-zinc-900 p-4">
        <div className="text-xs font-medium text-zinc-300">Поверки за квартал, по производителям</div>
        {rows.map(([n, v]) => (
          <div key={n} className="grid grid-cols-[110px_1fr_36px] items-center gap-2 text-[11px] text-zinc-500">
            <span className="truncate">{n}</span>
            <Bar value={v} />
            <span className="text-right tabular-nums">{v}k</span>
          </div>
        ))}
      </div>
      <div className="space-y-2 bg-zinc-900 p-4 text-[11px]">
        <div className="text-xs font-medium text-zinc-300">Клик по числу → детализация</div>
        <div className="flex items-center justify-between rounded-lg bg-white/[0.04] px-2.5 py-1.5 text-zinc-400">Учтено <b className="text-emerald-300">12 480</b></div>
        <div className="flex items-center justify-between rounded-lg bg-white/[0.04] px-2.5 py-1.5 text-zinc-400">Исключено <b className="text-amber-300">1 130</b></div>
        <div className="text-zinc-600">причины: непригодные, аннулированные, не в отчёте…</div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ Каталог */

function CatalogCard() {
  return (
    <div className="grid gap-px bg-white/[0.06] sm:grid-cols-[1.2fr_1fr]">
      <div className="bg-zinc-900 p-4">
        <div className="flex items-center gap-2">
          <Boxes size={16} className="text-zinc-400" />
          <span className="text-sm font-semibold text-zinc-100">МИРТЕК · Модель 3ф-xx</span>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5 text-[11px]">
          {[["Класс точности", "0,5S"], ["Номинальный ток", "5(10) А"], ["Интерфейс", "RS-485, PLC"], ["МПИ", "16 лет"], ["Код СИ", "№ 12345-26"]].map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-zinc-500">{k}</dt>
              <dd className="text-zinc-200">{v}</dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="space-y-1.5 bg-zinc-900 p-4 text-[11px]">
        <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-zinc-300"><Database size={13} /> Источники данных</div>
        {[["Сайт производителя", "прочитан", "emerald"], ["Описание типа (ФГИС)", "прочитано", "emerald"], ["Руководство", "сайт закрыт — загрузите PDF", "amber"]].map(([n, s, tone]) => (
          <div key={n} className="flex items-center justify-between gap-2 rounded-lg bg-white/[0.04] px-2.5 py-1.5">
            <span className="text-zinc-400">{n}</span>
            <Pill tone={tone as "emerald" | "amber"}>{s}</Pill>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- Планировщик */

function Scheduler() {
  const events = [
    { t: "03:15", d: "Чистка журналов", c: "bg-zinc-600" },
    { t: "06:00", d: "Опрос площадок + каталог", c: "bg-indigo-500" },
    { t: "06:30", d: "Письма о сроках", c: "bg-sky-500" },
    { t: "20:00", d: "Опрос площадок + каталог", c: "bg-indigo-500" },
  ];
  return (
    <div className="p-4">
      <div className="relative mb-4 h-1.5 rounded-full bg-white/[0.06]">
        {events.map((e) => {
          const [h, m] = e.t.split(":").map(Number);
          return (
            <span
              key={e.t + e.d}
              className={`absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-4 ring-zinc-900 ${e.c}`}
              style={{ left: `${((h * 60 + m) / 1440) * 100}%` }}
            />
          );
        })}
        <span className="absolute -bottom-5 left-0 text-[10px] text-zinc-600">00:00</span>
        <span className="absolute -bottom-5 left-1/2 -translate-x-1/2 text-[10px] text-zinc-600">12:00</span>
        <span className="absolute -bottom-5 right-0 text-[10px] text-zinc-600">24:00</span>
      </div>
      <div className="mt-8 grid gap-2 sm:grid-cols-2">
        {events.map((e) => (
          <div key={e.t + e.d} className="flex items-center gap-2 rounded-lg border border-white/10 px-3 py-2 text-xs">
            <span className={`h-2 w-2 rounded-full ${e.c}`} />
            <span className="tabular-nums text-zinc-300">{e.t}</span>
            <span className="text-zinc-500">{e.d}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 text-[11px] text-zinc-600">Время московское. Кроме того: каждые 15 минут — проверка доступности, каждые 30 — отслеживание изменений.</div>
    </div>
  );
}

/* ----------------------------------------------------------------------- API */

function ApiFlow() {
  return (
    <div className="grid items-center gap-3 p-4 sm:grid-cols-[1fr_auto_1.3fr_auto_1fr]">
      <div className="rounded-xl border border-white/10 p-3 text-center">
        <Server size={18} className="mx-auto text-zinc-400" />
        <div className="mt-1 text-xs font-semibold text-zinc-200">Ваша система</div>
        <div className="text-[10.5px] text-zinc-500">CRM · BI · скрипт</div>
      </div>
      <ArrowRight size={16} className="mx-auto rotate-90 text-zinc-600 sm:rotate-0" />
      <div className="rounded-xl border border-indigo-400/30 bg-indigo-500/[0.06] p-3 text-center">
        <KeyRound size={18} className="mx-auto text-indigo-300" />
        <code className="mt-1 block text-[11px] text-indigo-200">X-API-Key: orct_…</code>
        <code className="block text-[11px] text-zinc-400">GET /api/integration/v1/tenders</code>
      </div>
      <ArrowRight size={16} className="mx-auto rotate-90 text-zinc-600 sm:rotate-0" />
      <div className="rounded-xl border border-white/10 p-3 text-center">
        <Database size={18} className="mx-auto text-zinc-400" />
        <div className="mt-1 text-xs font-semibold text-zinc-200">JSON</div>
        <div className="text-[10.5px] text-zinc-500">items · total · limit · offset</div>
      </div>
    </div>
  );
}

/* -------------------------------------------------------------- Меню аккаунта */

function AccountMenu() {
  const Row = ({ children, tone = "" }: { children: ReactNode; tone?: string }) => (
    <div className={`flex h-9 items-center gap-2.5 rounded-xl px-3 text-xs font-medium ${tone || "text-zinc-300"}`}>{children}</div>
  );
  return (
    <div className="grid items-center gap-4 p-4 sm:grid-cols-[260px_1fr]">
      <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-2">
        <Row><Settings size={14} className="text-zinc-500" />Настройки учётной записи</Row>
        <Row><ScrollText size={14} className="text-zinc-500" />История обновлений</Row>
        <Row><Bell size={14} className="text-zinc-500" />Тема: Тёмная / Светлая</Row>
        <div className="mx-2 my-1 h-px bg-white/[0.06]" />
        <div className="rounded-xl bg-indigo-500/15 ring-1 ring-indigo-400/40">
          <Row tone="text-indigo-200"><FileText size={14} />Сова Wiki<ArrowRight size={13} className="ml-auto" /></Row>
        </div>
        <Row tone="text-red-400">Выйти</Row>
      </div>
      <div className="text-xs leading-relaxed text-zinc-400">
        <Callout n={1}>Кнопка <b className="text-zinc-200">«Сова Wiki»</b> расположена прямо над «Выйти» — откройте меню по аватару внизу боковой панели.</Callout>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------- Нет в списке */

function NotInList() {
  const steps = [
    ["Поиск по номеру", "нашлась? читайте пометку"],
    ["«Показать все»", "срок или профиль"],
    ["Профиль", "личный заменяет общие; ОКПД2 «узко»"],
    ["Только подобранные ИИ", "снимите галочку"],
    ["Воронка", "где число упало"],
    ["Площадка", "выбрана, доступна?"],
    ["Синхронизировать", "закупка свежая?"],
  ];
  return (
    <div className="grid gap-2 p-4 sm:grid-cols-2">
      {steps.map(([t, d], i) => (
        <div key={t} className="flex items-start gap-2.5 rounded-xl border border-white/10 p-2.5">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-500/20 text-[10px] font-semibold text-indigo-200">{i + 1}</span>
          <div>
            <div className="text-xs font-semibold text-zinc-200">{t}</div>
            <div className="text-[11px] text-zinc-500">{d}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

const FIGURES: Record<WikiFigure, { title: string; render: () => ReactNode }> = {
  pipeline: { title: "Sova · путь закупки", render: Pipeline },
  sidebar: { title: "Sova · боковая панель", render: Sidebar },
  roles: { title: "Sova · роли", render: Roles },
  "tender-list": { title: "Sova · Тендеры", render: TenderList },
  kanban: { title: "Sova · Тендеры · Kanban", render: Kanban },
  funnel: { title: "Sova · воронка отбора", render: Funnel },
  profile: { title: "Sova · профиль отбора", render: Profile },
  stages: { title: "Sova · этапы и отметки", render: Stages },
  "card-tabs": { title: "Sova · карточка закупки", render: CardTabs },
  mira: { title: "Sova · Мира", render: MiraFigure },
  matrix: { title: "Sova · Конкуренция", render: Matrix },
  "ai-score": { title: "Sova · AI-оценка", render: AiScore },
  "bitrix-flow": { title: "Sova → Bitrix24", render: BitrixFlow },
  analytics: { title: "Sova · Аналитика · Тендеры", render: Analytics },
  market: { title: "Sova · Аналитика · Рынок ПУ", render: Market },
  "catalog-card": { title: "Sova · Каталог продукции", render: CatalogCard },
  scheduler: { title: "Sova · расписание", render: Scheduler },
  "api-flow": { title: "API", render: ApiFlow },
  "account-menu": { title: "Sova · меню аккаунта", render: AccountMenu },
  "not-in-list": { title: "Sova · диагностика", render: NotInList },
};

export function WikiFigureView({ name, caption }: { name: WikiFigure; caption: string }) {
  const fig = FIGURES[name];
  const Render = fig.render;
  return (
    <figure className="my-7">
      <Window title={fig.title}>
        <Render />
      </Window>
      <figcaption className="mt-2.5 px-1 text-[13px] leading-snug text-zinc-500">{caption}</figcaption>
    </figure>
  );
}
