import { useEffect } from "react";
import type { ReactNode } from "react";
import { Database, Filter, Search, SlidersHorizontal, Sparkles, X } from "lucide-react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";

import type { SelectionFunnel } from "../api/types";
import { dialogVariants, scrimVariants } from "../utils/motion";

/**
 * «Как отбираются тендеры» (05.10.2026) — путь закупки от площадки до списка, по слоям.
 *
 * Пользователи видели три настраиваемые вещи (профиль релевантности, личные профили, проверку
 * ИИ) и не понимали, какая из них прячет нужную закупку и какая пропускает мусор. Схема
 * показывает слои в том порядке, в каком их применяет система, с живыми числами из воронки
 * (`GET /tenders/funnel`) и с тем, где каждый слой настраивается.
 */

interface Step {
  key: string;
  icon: ReactNode;
  title: string;
  what: string;
  where: ReactNode;
  count?: number | null;
  countLabel?: string;
}

const fmt = (value: number) => value.toLocaleString("ru-RU");

function StepRow({ step, index, isLast }: { step: Step; index: number; isLast: boolean }) {
  return (
    <li className="relative grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-4 md:grid-cols-[2.5rem_minmax(0,1fr)_14rem]">
      {/* Номер слоя и линия к следующему — путь закупки читается сверху вниз. */}
      <div className="flex flex-col items-center">
        <span className="relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-400/25">
          {step.icon}
        </span>
        {!isLast && <span className="mt-1 w-px flex-1 bg-gradient-to-b from-indigo-400/30 to-white/[0.06]" />}
      </div>
      <div className={`min-w-0 ${isLast ? "pb-1" : "pb-6"}`}>
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-[11px] font-medium uppercase tracking-wide text-zinc-500">
            Слой {index + 1}
          </span>
          <span className="text-[15px] font-semibold text-zinc-100">{step.title}</span>
        </div>
        <p className="mt-1 max-w-3xl text-sm leading-relaxed text-zinc-400">{step.what}</p>
        <div className="mt-2 text-xs text-zinc-500 md:hidden">{step.where}</div>
      </div>
      <div className={`hidden flex-col items-end text-right md:flex ${isLast ? "pb-1" : "pb-6"}`}>
        {step.count !== undefined && step.count !== null ? (
          <>
            <span className="text-2xl font-semibold tabular-nums leading-none text-zinc-50">
              {fmt(step.count)}
            </span>
            <span className="mt-1 text-[11px] text-zinc-500">{step.countLabel}</span>
          </>
        ) : null}
        <div className="mt-2 text-xs leading-relaxed text-zinc-500">{step.where}</div>
      </div>
    </li>
  );
}

export function SelectionGuide({
  funnel,
  compact = false,
  onOpenProfiles,
  onOpenFilters,
}: {
  funnel?: SelectionFunnel | null;
  /** Только схема, без раздела «как настроить». */
  compact?: boolean;
  onOpenProfiles?: () => void;
  onOpenFilters?: () => void;
}) {
  const settingsLink = (label: string, anchor: string) => (
    <Link to={`/settings?tab=relevance#${anchor}`} className="text-indigo-400 hover:text-indigo-300">
      {label}
    </Link>
  );
  const action = (label: string, onClick: (() => void) | undefined, fallback: ReactNode) =>
    onClick ? (
      <button type="button" onClick={onClick} className="text-indigo-400 hover:text-indigo-300">
        {label}
      </button>
    ) : (
      fallback
    );

  const aiHidden = funnel && funnel.ai_enabled ? funnel.after_profiles - funnel.after_ai : null;

  const steps: Step[] = [
    {
      key: "sources",
      icon: <Database size={18} />,
      title: "Площадки и каналы",
      what: "Система опрашивает площадки (ЕИС, Сбербанк-АСТ, РТС…) и внешние каналы (Госплан, Тендерплан) — утром и днём или по кнопке «Синхронизировать».",
      where: (
        <>
          Где:{" "}
          <Link to="/settings?tab=sources" className="text-indigo-400 hover:text-indigo-300">
            Настройки → Источники
          </Link>
        </>
      ),
      count: funnel?.collected,
      countLabel: "собрано в канале",
    },
    {
      key: "terms",
      icon: <Search size={18} />,
      title: "Что ищем на площадках",
      what: "Фразы, с которыми система спрашивает площадки. Закупка, которой нет ни по одной фразе, в базу не попадёт вовсе — профили её уже не увидят.",
      where: <>Где: {settingsLink("Отбор тендеров → Что ищем", "relevance-terms")} (администратор)</>,
    },
    {
      key: "profiles",
      icon: <SlidersHorizontal size={18} />,
      title: "Профили отбора",
      what: "Слова, исключения и коды ОКПД2. Общие профили действуют у всех по умолчанию; можно выбрать свои. Профиль привязывается к площадкам и сужает только их.",
      where: (
        <>
          Где:{" "}
          {action(
            "меню «Профиль» на странице тендеров",
            onOpenProfiles,
            settingsLink("Отбор тендеров → Профили", "relevance-profiles"),
          )}
        </>
      ),
      count: funnel?.after_profiles,
      countLabel: "прошли профили",
    },
    {
      key: "ai",
      icon: <Sparkles size={18} />,
      title: "Проверка моделью",
      what: "Модель читает название и отсекает то, что похоже по словам, но не наше: «счётчик монет», «счётчик клеток». Непроверенное не скрывается.",
      where: <>Где: галочка «Скрывать отклонённые ИИ» в «Фильтрах»</>,
      count: aiHidden,
      countLabel: "скрыла модель",
    },
    {
      key: "filters",
      icon: <Filter size={18} />,
      title: "Фильтры",
      what: "Срок подачи, сумма, регион, коды ОКПД2, теги — сужают то, что вы смотрите сейчас, и ничего не меняют у коллег.",
      where: (
        <>
          Где:{" "}
          {action("кнопка «Фильтры»", onOpenFilters, <>кнопка «Фильтры» на странице тендеров</>)}
        </>
      ),
      count: funnel?.shown,
      countLabel: "в списке",
    },
  ];

  return (
    <div className="space-y-4">
      <ol className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5">
        {steps.map((step, index) => (
          <StepRow key={step.key} step={step} index={index} isLast={index === steps.length - 1} />
        ))}
      </ol>
      <p className="text-xs text-zinc-500">
        Дальше — разбор открытой закупки: требования из ТЗ, матрица соответствия и заключение ИИ
        «подходим ли мы и каким прибором». Он запускается кнопкой «Разобрать» в карточке.
      </p>

      {!compact && (
        <div className="grid gap-3 md:grid-cols-3">
          <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4">
            <div className="mb-1.5 text-sm font-medium text-zinc-100">Как добавить термин</div>
            <ul className="space-y-1.5 text-xs leading-relaxed text-zinc-400">
              <li>
                <b className="text-zinc-300">Чтобы закупки вообще собирались</b> — фраза сбора
                («поверка приборов учета»). Обычный текст, без звёздочек: так ищут площадки.
              </li>
              <li>
                <b className="text-zinc-300">Чтобы отбирать собранное</b> — ключевое слово в
                профиле. <code>счетчик*</code> — все формы слова, <code>(замен* счетчик*)~3</code> —
                слова рядом. Enter добавляет термин; ниже сразу видно, сколько закупок он отберёт и
                какие.
              </li>
              <li>
                <b className="text-zinc-300">Чтобы отсечь мусор</b> — исключение: <code>вод*</code>,{" "}
                <code>газ*</code>. Встретилось — профиль закупку не берёт.
              </li>
            </ul>
          </div>
          <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4">
            <div className="mb-1.5 text-sm font-medium text-zinc-100">
              Как применить профиль к площадкам
            </div>
            <ul className="space-y-1.5 text-xs leading-relaxed text-zinc-400">
              <li>
                В профиле — блок «Площадки»: отметьте нужные. Ничего не отмечено — профиль действует
                на все.
              </li>
              <li>
                Один профиль можно привязать к нескольким площадкам, на одну площадку — несколько
                профилей.
              </li>
              <li>
                Несколько профилей на одной площадке сочетаются: «любой» — закупка подошла хотя бы
                одному, «все» — всем сразу. Переключатель — в меню «Профиль».
              </li>
              <li>Закупки площадок, к которым не привязан ни один выбранный профиль, не сужаются.</li>
            </ul>
          </div>
          <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-4">
            <div className="mb-1.5 text-sm font-medium text-zinc-100">Почему закупки нет в списке</div>
            <ul className="space-y-1.5 text-xs leading-relaxed text-zinc-400">
              <li>
                Посмотрите строку над списком: «собрано → профили → модель → фильтры». Где число
                резко падает, там закупка и отсеялась.
              </li>
              <li>Нет даже в «собрано» — не было подходящей фразы сбора или площадка не опрошена.</li>
              <li>
                Отсеял профиль — откройте его: в предпросмотре видно, что он берёт, а что нет;
                добавьте слово или уберите лишнее исключение.
              </li>
            </ul>
          </div>
        </div>
      )}
    </div>
  );
}

/** Гайд в окне — со страницы тендеров, с числами текущей выдачи. */
export function SelectionGuideModal({
  funnel,
  onClose,
  onOpenProfiles,
  onOpenFilters,
}: {
  funnel: SelectionFunnel | null;
  onClose: () => void;
  onOpenProfiles?: () => void;
  onOpenFilters?: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <motion.div
      variants={scrimVariants}
      initial="initial"
      animate="animate"
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/60 px-4 py-8"
      onClick={onClose}
    >
      <motion.div
        variants={dialogVariants}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Как отбираются тендеры"
        className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-xl border border-white/10 bg-zinc-900 shadow-xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-white/[0.08] px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-white">Как отбираются тендеры</h2>
            <p className="mt-0.5 text-xs text-zinc-500">
              Путь закупки от площадки до вашего списка. Числа — для текущего канала и фильтров.
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Закрыть"
            className="rounded-md p-1 text-zinc-500 hover:bg-white/10 hover:text-zinc-100"
          >
            <X size={16} />
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-4">
          <SelectionGuide
            funnel={funnel}
            onOpenProfiles={
              onOpenProfiles &&
              (() => {
                onClose();
                onOpenProfiles();
              })
            }
            onOpenFilters={
              onOpenFilters &&
              (() => {
                onClose();
                onOpenFilters();
              })
            }
          />
        </div>
      </motion.div>
    </motion.div>
  );
}
