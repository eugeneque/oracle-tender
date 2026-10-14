import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  BrainCircuit,
  Check,
  Coins,
  GitMerge,
  Play,
  RotateCcw,
  Scale,
  ShieldAlert,
  Timer,
  X,
} from "lucide-react";

import { SegmentedControl } from "../ui/SegmentedControl";
import { EASE_OUT, SPRING_SNAPPY, staggerContainer, staggerItem } from "../../utils/motion";

// Блок «Обновления модели» на странице «Что нового?» (14.10.2026): насколько поумнел
// ИИ-разбор — не списком, а карточками, которые можно потрогать. Переключатель «Было / Стало»
// перестраивает все карточки сразу; у части карточек свои действия (проверить название,
// свести дубли, пересчитать оценку, запустить разбор). Цифры — из сверок, описанных в
// ARCHITECTURE.md и журнале изменений; примеры закупок и проценты производителей — иллюстрация.

type Era = "before" | "after";

// ——— Правила ИИ-отбора без модели ————————————————————————————————————————————————
// Перенос `rule_answer` из backend/app/services/ai_relevance_service.py — держать в согласии.
// `\w` и `\b` в JS не знают кириллицы, поэтому вместо них — явный класс букв.
const L = "[а-яa-z0-9_]";
const METER_RE = new RegExp(
  `(электро\\s*сч[её]тчик|сч[её]тчик${L}*\\s+(активной\\s+|реактивной\\s+)?(электр|э/э)` +
    `|прибор${L}*\\s+(коммерческого\\s+|технического\\s+)?уч[её]та\\s+(электр|э/э)` +
    `|аскуэ|аиис\\s*куэ)`,
);
const ACTION_RE = /^(\S+\s+){0,3}?(на\s+)?(право\s+заключения\s+договора\s+на\s+)?(поставк|приобретени|закупк|замен|поверк)/;
const NOT_SURE_RE = new RegExp(
  `(строительств|смр|пир(?!${L})|реконструкц|испытательн|коробк|модул|журнал|расходн|опор` +
    `|снят${L}*\\s+показ|информац|диагност|вод|газ|тепл|жидкост|аренд` +
    `|ремонт\\s+(здани|помещ)|трансформатор|кабел|провод|щит|шкаф|пломб|печат|программн` +
    `|лицензи|материал|сервер|компьютер|обогрев|комплектующ|запасн|зип(?!${L})` +
    `|для\\s+(монтажа|работы|оборудования|обслуживания)|услуг${L}*\\s+по\\s+(сбор|передач))`,
);
const OTHER_RESOURCE_RE = new RegExp(
  `(сч[её]тчик|прибор${L}*\\s+уч[её]та|узл${L}*\\s+уч[её]та|расходомер)${L}*[^.;]{0,40}` +
    `(вод|газ|тепл|жидкост|частиц|семян|аэрозол)`,
);

type RuleVerdict = "ours" | "not-ours" | "model";

function ruleVerdict(raw: string): RuleVerdict {
  const title = raw.toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();
  if (!title) return "model";
  if (!title.includes("электр") && !title.includes("э/э") && OTHER_RESOURCE_RE.test(title)) return "not-ours";
  if (title.length <= 250 && METER_RE.test(title) && ACTION_RE.test(title) && !NOT_SURE_RE.test(title)) return "ours";
  return "model";
}

const RULE_EXAMPLES = [
  "Поставка электросчётчиков МИРТЕК",
  "Поставка счётчиков холодной воды",
  "Замена приборов учёта электроэнергии в МКД",
  "Строительство ВЛ-0,4 кВ с установкой приборов учёта электроэнергии",
  "Материалы для монтажа электросчётчиков",
];

// ——— Общие детали ——————————————————————————————————————————————————————————————

function Card({
  icon,
  kicker,
  title,
  className = "",
  action,
  children,
}: {
  icon: ReactNode;
  kicker: string;
  title: string;
  className?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <motion.section
      variants={staggerItem}
      className={`flex flex-col rounded-[28px] border border-white/[0.07] bg-zinc-900/70 p-6 ${className}`}
    >
      <div className="mb-4">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-indigo-500/15 text-indigo-300">
            {icon}
          </span>
          <span className="min-w-0 flex-1 truncate text-[11px] font-medium uppercase tracking-wider text-zinc-500">{kicker}</span>
          {action}
        </div>
        <h3 className="mt-3 text-[17px] font-semibold leading-snug tracking-tight text-zinc-100">{title}</h3>
      </div>
      <div className="flex flex-1 flex-col">{children}</div>
    </motion.section>
  );
}

function ActionButton({ onClick, children, disabled }: { onClick: () => void; children: ReactNode; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-white/[0.06] px-3 py-1.5 text-xs font-medium text-zinc-200 transition-colors hover:bg-white/[0.1] disabled:opacity-40"
    >
      {children}
    </button>
  );
}

/** Значение, которое при смене «Было / Стало» уезжает вверх и сменяется новым. */
function Swap({ value, className = "" }: { value: ReactNode; className?: string }) {
  return (
    <span className={`relative inline-flex overflow-hidden ${className}`}>
      <AnimatePresence mode="popLayout" initial={false}>
        <motion.span
          key={String(value)}
          initial={{ y: "70%", opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={{ y: "-70%", opacity: 0 }}
          transition={{ duration: 0.35, ease: EASE_OUT }}
        >
          {value}
        </motion.span>
      </AnimatePresence>
    </span>
  );
}

const Footnote = ({ children }: { children: ReactNode }) => (
  <p className="mt-auto pt-4 text-xs leading-relaxed text-zinc-500">{children}</p>
);

// ——— Сводка вверху ———————————————————————————————————————————————————————————————

const METRICS: { label: string; before: string; after: string; hint: string }[] = [
  { label: "закупок ИИ-отбор решает без модели", before: "0%", after: "28%", hint: "сверка на 933 размеченных закупках, расхождений с моделью — 0" },
  { label: "полный разбор на DeepSeek", before: "18 мин", after: "3,5 мин", hint: "без режима «рассуждений»" },
  { label: "токенов на запрос матрицы", before: "100%", after: "50%", hint: "только нужные пачке сведения о производителе" },
  { label: "разброс AI-оценки одной закупки", before: "до 50 п.", after: "0 п.", hint: "статусы ставит модель, процент считает код" },
];

function MetricStrip({ era }: { era: Era }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {METRICS.map((m) => (
        <motion.div
          key={m.label}
          variants={staggerItem}
          title={m.hint}
          className="rounded-[22px] border border-white/[0.07] bg-zinc-900/70 px-5 py-4"
        >
          <Swap
            value={era === "before" ? m.before : m.after}
            className={`text-3xl font-semibold tabular-nums tracking-tight ${era === "after" ? "text-indigo-300" : "text-zinc-400"}`}
          />
          <div className="mt-1 text-xs leading-snug text-zinc-400">{m.label}</div>
        </motion.div>
      ))}
    </div>
  );
}

// ——— 1. Решение без модели: живая проверка названия ——————————————————————————————————

const VERDICT_VIEW: Record<RuleVerdict, { label: string; note: string; cls: string; icon: ReactNode }> = {
  ours: {
    label: "Наша — решено правилом",
    note: "Поставка, замена или поверка приборов учёта электроэнергии. Модель не вызывается.",
    cls: "bg-emerald-500/15 text-emerald-200 ring-emerald-400/40",
    icon: <Check size={15} />,
  },
  "not-ours": {
    label: "Не наша — решено правилом",
    note: "Прибор учёта воды, газа, тепла или другого ресурса. Модель не вызывается.",
    cls: "bg-rose-500/15 text-rose-200 ring-rose-400/40",
    icon: <X size={15} />,
  },
  model: {
    label: "Решает модель",
    note: "Название неоднозначно — счётчики могут быть частью стройки, материалов или другого ресурса.",
    cls: "bg-indigo-500/15 text-indigo-200 ring-indigo-400/40",
    icon: <BrainCircuit size={15} />,
  },
};

function RuleCard({ era }: { era: Era }) {
  const [title, setTitle] = useState(RULE_EXAMPLES[0]);
  const verdict: RuleVerdict = era === "before" ? "model" : ruleVerdict(title);
  const view = VERDICT_VIEW[verdict];

  return (
    <Card
      icon={<BrainCircuit size={18} />}
      kicker="ИИ-отбор · попробуйте"
      title="Однозначное решается без модели"
      className="lg:col-span-2"
    >
      <label className="sr-only" htmlFor="rule-title">Название закупки</label>
      <input
        id="rule-title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Введите название закупки"
        className="w-full rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3 text-[15px] text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-indigo-400/60"
      />
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {RULE_EXAMPLES.map((example) => (
          <button
            key={example}
            type="button"
            onClick={() => setTitle(example)}
            className={`max-w-full truncate rounded-full px-2.5 py-1 text-[11px] transition-colors ${
              example === title ? "bg-indigo-500/20 text-indigo-200" : "bg-white/[0.05] text-zinc-400 hover:bg-white/[0.09] hover:text-zinc-200"
            }`}
          >
            {example}
          </button>
        ))}
      </div>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={`${era}-${verdict}`}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.25, ease: EASE_OUT }}
          className="mt-4 flex items-start gap-3"
        >
          <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium ring-1 ${view.cls}`}>
            {view.icon} {view.label}
          </span>
          <span className="pt-1 text-sm text-zinc-400">
            {era === "before" ? "Раньше каждая закупка уходила в модель — даже «Поставка электросчётчиков»." : view.note}
          </span>
        </motion.div>
      </AnimatePresence>
      <div className="mt-5">
        <div className="mb-1.5 flex justify-between text-[11px] text-zinc-500">
          <span>933 закупки, уже размеченные моделью</span>
          <span className="tabular-nums">{era === "after" ? "265 без модели · 0 расхождений" : "все — через модель"}</span>
        </div>
        <div className="flex h-2.5 overflow-hidden rounded-full bg-indigo-500/25">
          <motion.span
            className="h-full bg-emerald-400"
            animate={{ width: era === "after" ? "28%" : "0%" }}
            transition={{ duration: 0.6, ease: EASE_OUT }}
          />
        </div>
      </div>
      <Footnote>Правила нарочно узкие: стройка, материалы, «для монтажа», другой ресурс — всё сомнительное по-прежнему решает модель.</Footnote>
    </Card>
  );
}

// ——— 2. Эталон ТЗ в «Конкуренции» ——————————————————————————————————————————————————

const REFERENCE_ROWS: { name: string; before: [number, string]; after: [number, string, string] }[] = [
  { name: "МИРТЕК-32-РУ", before: [100, "проходит"], after: [100, "эталон ТЗ", "bg-emerald-400"] },
  { name: "Энергомера CE308", before: [100, "проходит"], after: [74, "эквивалент · с оговорками", "bg-amber-400"] },
  { name: "Инкотекс Меркурий 234", before: [100, "проходит"], after: [68, "эквивалент · с оговорками", "bg-amber-400"] },
  { name: "Матрица NP73", before: [100, "проходит"], after: [41, "не проходит", "bg-rose-400"] },
];

function ReferenceCard({ era }: { era: Era }) {
  return (
    <Card icon={<Scale size={18} />} kicker="Конкуренция" title="Видит, под чью модель написано ТЗ">
      <div className="space-y-2.5">
        {REFERENCE_ROWS.map((row) => {
          const [pct, note] = era === "before" ? row.before : row.after;
          const bar = era === "before" ? "bg-zinc-500" : row.after[2];
          return (
            <div key={row.name}>
              <div className="mb-1 flex items-baseline gap-2 text-[13px]">
                <span className="min-w-0 flex-1 truncate text-zinc-200">{row.name}</span>
                <Swap value={`${pct}%`} className="w-10 justify-end tabular-nums text-zinc-300" />
              </div>
              <div className="h-1.5 rounded-full bg-white/[0.06]">
                <motion.div
                  className={`h-full rounded-full ${bar}`}
                  animate={{ width: `${pct}%` }}
                  transition={{ duration: 0.6, ease: EASE_OUT }}
                />
              </div>
              <Swap value={note} className="mt-1 text-[11px] text-zinc-500" />
            </div>
          );
        })}
      </div>
      <Footnote>
        {era === "before"
          ? "ТЗ «МИРТЕК-32-РУ или эквивалент» — и все четверо «проходят» на 100%, как будто разницы нет."
          : "Эталон находится в тексте ТЗ и сверяется с каталогом; остальные — только эквивалент, а при запрете эквивалента — «не проходит»."}
      </Footnote>
    </Card>
  );
}

// ——— 3. Дубли требований ———————————————————————————————————————————————————————————

const RAW_REQUIREMENTS = [
  { id: "a", text: "Класс точности не ниже 1,0", source: "ТЗ, п. 2.1", group: "accuracy", level: "важное" },
  { id: "b", text: "Класс точности 1 по активной энергии", source: "проект договора", group: "accuracy", level: "критичное" },
  { id: "c", text: "Интерфейс RS-485", source: "ТЗ, п. 3.4", group: "rs", level: "важное" },
  { id: "d", text: "Наличие интерфейса RS-485", source: "спецификация", group: "rs", level: "важное" },
  { id: "e", text: "Класс точности — 1", source: "спецификация", group: "accuracy", level: "важное" },
];

function DuplicatesCard({ era }: { era: Era }) {
  const [merged, setMerged] = useState(era === "after");
  useEffect(() => setMerged(era === "after"), [era]);

  const rows = useMemo(() => {
    if (!merged) return RAW_REQUIREMENTS.map((r) => ({ ...r, count: 1 }));
    // Сводится в одно требование с наибольшей критичностью — как в разборе ТЗ.
    const byGroup = new Map<string, (typeof RAW_REQUIREMENTS)[number] & { count: number }>();
    for (const r of RAW_REQUIREMENTS) {
      const kept = byGroup.get(r.group);
      if (!kept) byGroup.set(r.group, { ...r, count: 1 });
      else byGroup.set(r.group, { ...(r.level === "критичное" ? r : kept), count: kept.count + 1 });
    }
    return [...byGroup.values()];
  }, [merged]);

  return (
    <Card
      icon={<GitMerge size={18} />}
      kicker="Разбор ТЗ"
      title="Без дублей требований"
      action={
        <ActionButton onClick={() => setMerged((m) => !m)}>
          {merged ? <RotateCcw size={13} /> : <GitMerge size={13} />}
          {merged ? "Как было" : "Свести"}
        </ActionButton>
      }
    >
      <motion.ul layout className="space-y-1.5">
        <AnimatePresence initial={false}>
          {rows.map((r) => (
            <motion.li
              key={r.group + (merged ? "" : r.id)}
              layout
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.96 }}
              transition={SPRING_SNAPPY}
              className={`flex items-center gap-2 rounded-xl px-3 py-2 text-[13px] ${
                r.count > 1 ? "bg-indigo-500/10 ring-1 ring-indigo-400/30" : "bg-white/[0.04]"
              }`}
            >
              <span className="min-w-0 flex-1 truncate text-zinc-200">{r.text}</span>
              <span className="shrink-0 text-[11px] text-zinc-500">{r.count > 1 ? `${r.count} источника` : r.source}</span>
              {r.level === "критичное" && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-rose-400" title="критичное" />}
            </motion.li>
          ))}
        </AnimatePresence>
      </motion.ul>
      <Footnote>
        Повторы из ТЗ, спецификации и проекта договора — одно требование с наибольшей критичностью:{" "}
        <Swap value={merged ? "2 строки" : "5 строк"} className="font-medium text-zinc-300" /> в матрице.
      </Footnote>
    </Card>
  );
}

// ——— 4. AI-оценка по чек-листам ————————————————————————————————————————————————————

// Прежняя оценка одной и той же закупки при повторных прогонах — см. журнал изменений
// («то 0, то 50»). После — модель ставит статусы пунктов, процент считает код.
const BEFORE_RUNS = [0, 50, 20, 50, 0, 35, 50, 10];
const AFTER_SCORE = 40;

function StabilityCard({ era }: { era: Era }) {
  const [runs, setRuns] = useState<number[]>([]);
  useEffect(() => setRuns([]), [era]);

  const run = () =>
    setRuns((prev) => (prev.length >= 8 ? [] : [...prev, era === "before" ? BEFORE_RUNS[prev.length] : AFTER_SCORE]));
  const last = runs[runs.length - 1];

  return (
    <Card
      icon={<ShieldAlert size={18} />}
      kicker="AI-оценка"
      title="Одна закупка — одна оценка"
      action={
        <ActionButton onClick={run}>
          {runs.length >= 8 ? <RotateCcw size={13} /> : <Play size={13} />}
          {runs.length >= 8 ? "Сначала" : "Оценить ещё раз"}
        </ActionButton>
      }
    >
      <div className="flex items-end gap-4">
        <Swap
          value={last === undefined ? "—" : `${last}%`}
          className={`text-5xl font-semibold tabular-nums tracking-tight ${era === "after" ? "text-indigo-300" : "text-zinc-300"}`}
        />
        <span className="pb-2 text-xs text-zinc-500">прогон {runs.length} из 8</span>
      </div>
      <div className="mt-4 flex h-20 items-end gap-1.5 rounded-xl bg-white/[0.03] p-2">
        {Array.from({ length: 8 }, (_, n) => (
          <span key={n} className="flex h-full flex-1 items-end">
            {runs[n] !== undefined && (
              <motion.span
                initial={{ height: 0 }}
                animate={{ height: `${Math.max(runs[n], 3)}%` }}
                transition={{ duration: 0.4, ease: EASE_OUT }}
                className={`w-full rounded-t-[4px] ${era === "after" ? "bg-indigo-400" : "bg-amber-400/80"}`}
              />
            )}
          </span>
        ))}
      </div>
      <Footnote>
        {era === "before"
          ? "Модель сама называла процент — одна и та же закупка получала то 0, то 50. Нажмите несколько раз."
          : "Модель ставит статусы пунктов чек-листа, процент считает код. И он согласован с заключением: при «не проходит» — не выше 20%."}
      </Footnote>
    </Card>
  );
}

// ——— 5. Скорость полного разбора ———————————————————————————————————————————————————

// Масштаб анимации: 18 минут — 6 секунд на экране.
const SECONDS_PER_MINUTE = 6 / 18;

function SpeedCard() {
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const frame = useRef<number>();

  useEffect(() => {
    if (startedAt === null) return;
    const tick = () => {
      const minutes = (performance.now() - startedAt) / 1000 / SECONDS_PER_MINUTE;
      setElapsed(Math.min(minutes, 18));
      if (minutes < 18) frame.current = requestAnimationFrame(tick);
    };
    frame.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame.current!);
  }, [startedAt]);

  const lanes = [
    { label: "Было: с «рассуждениями»", total: 18, cls: "bg-zinc-500" },
    { label: "Стало: без «рассуждений»", total: 3.5, cls: "bg-indigo-400" },
  ];

  return (
    <Card
      icon={<Timer size={18} />}
      kicker="DeepSeek"
      title="Полный разбор в 5 раз быстрее"
      action={
        <ActionButton onClick={() => { setElapsed(0); setStartedAt(performance.now()); }}>
          <Play size={13} /> {startedAt === null ? "Запустить" : "Ещё раз"}
        </ActionButton>
      }
    >
      <div className="space-y-4">
        {lanes.map((lane) => {
          const done = Math.min(elapsed, lane.total);
          const finished = elapsed >= lane.total && startedAt !== null;
          return (
            <div key={lane.label}>
              <div className="mb-1.5 flex items-baseline justify-between text-[13px]">
                <span className="text-zinc-300">{lane.label}</span>
                <span className={`tabular-nums ${finished ? "text-zinc-100" : "text-zinc-500"}`}>
                  {done.toFixed(1).replace(".", ",")} мин{finished && " ✓"}
                </span>
              </div>
              <div className="h-2.5 rounded-full bg-white/[0.06]">
                <div className={`h-full rounded-full ${lane.cls}`} style={{ width: `${(done / 18) * 100}%` }} />
              </div>
            </div>
          );
        })}
      </div>
      <Footnote>Анализ документов, матрица соответствия и оценка цепочкой. Пока старый разбор идёт, новый успевает пройти пять закупок.</Footnote>
    </Card>
  );
}

// ——— 6. Заключение: ТЗ под чужой товарный знак ———————————————————————————————————————

function ConclusionCard({ era }: { era: Era }) {
  const before = era === "before";
  return (
    <Card icon={<BrainCircuit size={18} />} kicker="Заключение ИИ · закупка 32616408289" title="Главная причина — настоящая">
      <div className="rounded-2xl bg-white/[0.04] p-4">
        <div className="flex items-center gap-2">
          <span className="rounded-full bg-rose-500/15 px-2.5 py-1 text-xs font-medium text-rose-200">не подходим</span>
          <span className="text-xs text-zinc-500">главная причина</span>
        </div>
        <AnimatePresence mode="wait" initial={false}>
          <motion.p
            key={era}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.25, ease: EASE_OUT }}
            className="mt-2.5 text-[15px] leading-snug text-zinc-100"
          >
            {before
              ? "Компания не является субъектом МСП."
              : "ТЗ написано под модели другого производителя, эквивалент не допускается."}
          </motion.p>
        </AnimatePresence>
      </div>
      <div className="mt-2 rounded-2xl bg-white/[0.03] px-4 py-2.5 text-[13px]">
        <div className="flex items-center gap-2">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${before ? "bg-rose-400" : "bg-amber-400"}`} />
          <span className="text-zinc-300">Требование МСП</span>
        </div>
        <Swap value={before ? "Отказ" : "Оговорка: подать от юрлица группы, которое МСП"} className="mt-0.5 pl-3.5 text-xs text-zinc-500" />
      </div>
      <Footnote>
        Статус МСП теперь сверяется со всеми юрлицами группы, а чужой товарный знак без эквивалента распознаётся как то, что действительно закрывает закупку.
      </Footnote>
    </Card>
  );
}

// ——— 7. Токены ——————————————————————————————————————————————————————————————————————

const TOKEN_ROWS = [
  { label: "Матрица соответствия, на запрос", after: 50, note: "только нужные сведения о производителе" },
  { label: "ИИ-отбор закупок", after: 72, note: "28% решается правилами" },
  { label: "«Описание типа» ФГИС", after: 84, note: "служебные разделы вырезаются до модели" },
  { label: "Плановый опрос каталога", after: 0, note: "дважды в день — без модели" },
];

function TokensCard({ era }: { era: Era }) {
  return (
    <Card icon={<Coins size={18} />} kicker="Расход" title="Те же ответы — меньше токенов" className="lg:col-span-2">
      <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
        {TOKEN_ROWS.map((row) => {
          const value = era === "before" ? 100 : row.after;
          return (
            <div key={row.label}>
              <div className="mb-1 flex items-baseline justify-between gap-2 text-[13px]">
                <span className="truncate text-zinc-200">{row.label}</span>
                <Swap value={`${value}%`} className="tabular-nums text-zinc-300" />
              </div>
              <div className="h-2 rounded-full bg-white/[0.06]">
                <motion.div
                  className={`h-full rounded-full ${era === "before" ? "bg-zinc-500" : "bg-indigo-400"}`}
                  animate={{ width: `${value}%` }}
                  transition={{ duration: 0.6, ease: EASE_OUT }}
                />
              </div>
              <Swap value={era === "before" ? "прежний расход" : row.note} className="mt-1 text-[11px] text-zinc-500" />
            </div>
          );
        })}
      </div>
      <Footnote>Доля от прежнего расхода. Подробно по дням, моделям и шагам — «Интеграции → Детализация ИИ».</Footnote>
    </Card>
  );
}

// ——— Блок целиком ———————————————————————————————————————————————————————————————————

export function ModelUpdates() {
  const [era, setEra] = useState<Era>("after");

  return (
    <motion.div variants={staggerContainer} initial="initial" animate="animate" className="space-y-3">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
        <p className="max-w-xl text-[15px] leading-relaxed text-zinc-400">
          Как изменился разбор закупок моделью за сентябрь — октябрь. Переключите «Было / Стало» и потрогайте карточки.
        </p>
        <SegmentedControl
          shape="pill"
          size="sm"
          ariaLabel="Сравнение"
          value={era}
          onChange={setEra}
          segments={[
            { value: "before", label: "Было" },
            { value: "after", label: "Стало" },
          ]}
        />
      </div>
      <MetricStrip era={era} />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <RuleCard era={era} />
        <ReferenceCard era={era} />
        <DuplicatesCard era={era} />
        <StabilityCard era={era} />
        <SpeedCard />
        <ConclusionCard era={era} />
        <TokensCard era={era} />
      </div>
    </motion.div>
  );
}
