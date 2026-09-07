export function daysLeft(applicationEnd: string | null): number | null {
  if (!applicationEnd) return null;
  // Сравниваем календарные даты (без времени суток): дедлайн "13.08" должен показывать 0 дн.
  // весь день 13-го, а не уходить в минус после полуночи — сравнение полных timestamp
  // заставляло "сегодня" мигать в "срок истёк" в течение того же дня.
  const [y, m, d] = applicationEnd.slice(0, 10).split("-").map(Number);
  const endMidnight = new Date(y, m - 1, d).getTime();
  const now = new Date();
  const todayMidnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return Math.round((endMidnight - todayMidnight) / (1000 * 60 * 60 * 24));
}

export function formatPrice(price: string | null, currency: string): string | null {
  if (price === null) return null;
  const value = Number(price);
  if (Number.isNaN(value)) return null;
  const formatted = new Intl.NumberFormat("ru-RU").format(value);
  return currency === "RUB" ? `${formatted} ₽` : `${formatted} ${currency}`;
}

/** Время в системе хранится в UTC, а показывается в МСК (раздел 8 ТЗ).
 *
 * Часовой пояс задаётся явно, а не берётся из браузера: пользователи работают из разных
 * регионов, а срок подачи заявки — общий для всех и объявлен по московскому времени. Без
 * этого дедлайн «17:00» превращался бы во «20:00» на дальневосточном ноутбуке, и разница
 * замечалась бы уже после срыва подачи. */
const MOSCOW_TZ = "Europe/Moscow";

/** Дата без времени суток (`date` в БД) приходит как «ГГГГ-ММ-ДД» и пояса не имеет.
 *
 * Такую строку `new Date()` разбирает как полночь UTC, и перевод в МСК сдвинул бы её на
 * следующий день не для всех, а только для части значений — поэтому календарная дата
 * форматируется без участия часовых поясов вовсе. */
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;

export function formatDate(value: string | null): string | null {
  if (!value) return null;
  if (DATE_ONLY_RE.test(value)) {
    const [year, month, day] = value.split("-");
    return `${day}.${month}.${year}`;
  }
  return new Date(value).toLocaleDateString("ru-RU", { timeZone: MOSCOW_TZ });
}

export function formatDateTime(value: string | null): string | null {
  if (!value) return null;
  return new Date(value).toLocaleString("ru-RU", { timeZone: MOSCOW_TZ });
}

const STATUS_LABELS: Record<string, string> = {
  collecting_bids: "Сбор заявок",
  evaluation: "Оценка",
  completed: "Завершено",
  cancelled: "Отменено",
};

export function statusLabel(status: string | null): string {
  if (!status) return "Без статуса";
  return STATUS_LABELS[status] ?? status;
}

const TENDER_TYPE_LABELS: Record<string, string> = {
  supply_only: "Поставка ИПУ",
  complex: "Комплекс (ПУ + работы)",
  works_only: "Работы (СМР и ПНР)",
  reverification: "Переповерка",
  other: "Прочее",
};

/** Тип конкурса — Приложение E ТЗ. */
export function tenderTypeLabel(type: string | null): string | null {
  if (!type) return null;
  return TENDER_TYPE_LABELS[type] ?? type;
}

const CRITICALITY_LABELS: Record<string, string> = {
  critical: "Критичное",
  important: "Важное",
  minor: "Второстепенное",
};

export function criticalityLabel(value: string): string {
  return CRITICALITY_LABELS[value] ?? value;
}

const COMPLIANCE_LABELS: Record<string, string> = {
  meets: "Соответствует",
  partial: "Частично",
  not_meets: "Не соответствует",
  no_data: "Нет данных",
};

export function complianceLabel(value: string): string {
  return COMPLIANCE_LABELS[value] ?? value;
}

/** Процент победителя приходит строкой (Numeric в БД) — к числу для отрисовки. */
export function percentValue(value: string | null): number | null {
  if (value === null) return null;
  const parsed = Number(value);
  return Number.isNaN(parsed) ? null : Math.round(parsed);
}

const COMPLIANCE_SOURCE_LABELS: Record<string, string> = {
  si_type: "Описание типа (ФГИС)",
  product_catalog: "Каталог продукции",
  user_manual_fallback: "Руководство пользователя",
  ai_semantic: "Вывод ИИ по смыслу",
};

/** Источник вердикта — три шага сопоставления раздела 5.5 ТЗ. */
export function complianceSourceLabel(value: string | null): string | null {
  if (!value) return null;
  return COMPLIANCE_SOURCE_LABELS[value] ?? value;
}

const RELEVANCE_LABELS: Record<string, string> = {
  new: "Не проверен",
  confirmed: "Релевантный",
  rejected: "Неактуальный",
};

export function relevanceLabel(value: string): string {
  return RELEVANCE_LABELS[value] ?? value;
}

/** Пороги цвета процента победителя — раздел 5.6 ТЗ: зелёный ≥80, жёлтый 50–80, красный <50. */
export function percentTextClass(percent: number): string {
  if (percent >= 80) return "text-emerald-400";
  if (percent >= 50) return "text-amber-400";
  return "text-red-400";
}

// Этап `ai_selected` показывается как «Новая» (04.09.2026), а не «AI отобрал». Прежняя
// подпись обманывала: это значение по умолчанию, которое получает КАЖДАЯ собранная закупка,
// никакого решения модели за ним нет. Решение модели — отдельная метка «Подобрано ИИ»
// (`tenders.ai_relevant`), и две разные вещи под похожими названиями путали работу.
const STAGE_LABELS_MAP: Record<string, string> = {
  ai_selected: "Новая",
  under_review: "На проверке",
  application_submitted: "Заявка подана",
  won: "Выиграли",
  lost: "Проиграли",
  rejected: "Отклонён",
};

/** Этап внутреннего пайплайна (раздел 5.6 ТЗ) — не путать со статусом закупки на площадке. */
export function stageLabel(value: string | null): string {
  if (!value) return "Без этапа";
  return STAGE_LABELS_MAP[value] ?? value;
}

/** Что означает этап — подсказка рядом с полем.
 *
 * Этап отвечает на вопрос «что МЫ с этой закупкой делаем», и ставит его человек. Это
 * ортогонально и статусу закупки на площадке («идёт приём заявок»), и решению модели
 * («Подобрано ИИ»): первое — состояние торгов, второе — мнение системы, а этап — наша работа.
 */
const STAGE_HINTS_MAP: Record<string, string> = {
  ai_selected:
    "Закупка собрана системой, человек её ещё не смотрел. Стартовое состояние — его получает каждый новый тендер.",
  under_review:
    "Взяли в работу: тендерный отдел разбирает документацию и решает, подавать ли заявку.",
  application_submitted:
    "Заявка подана. По этому этапу система потом определяет проигрыши: если контракт достался не нам, участие запишется как проигранное.",
  won: "Контракт наш.",
  lost: "Участвовали и проиграли по существу.",
  rejected: "Не идём: закупка не подходит или решили не участвовать.",
};

export function stageHint(value: string | null): string | null {
  if (!value) return null;
  return STAGE_HINTS_MAP[value] ?? null;
}

const VERDICT_LABELS_MAP: Record<string, string> = {
  go: "ИДТИ",
  go_with_reservations: "ИДТИ С ОГОВОРКАМИ",
  no_go: "НЕ ИДТИ",
};

export function verdictLabel(value: string | null): string | null {
  if (!value) return null;
  return VERDICT_LABELS_MAP[value] ?? value;
}

/** Цвет бейджа AI-оценки. Те же пороги, что у процента соответствия (раздел 5.6 ТЗ):
 * зелёный ≥80%, жёлтый 50–80%, красный <50%. */
export function scoreBadgeClass(percent: number | null): string {
  if (percent === null) return "border-white/10 bg-white/5 text-zinc-500";
  if (percent >= 80) return "border-emerald-500/30 bg-emerald-500/10 text-emerald-300";
  if (percent >= 50) return "border-amber-500/30 bg-amber-500/10 text-amber-300";
  return "border-red-500/30 bg-red-500/10 text-red-300";
}

/** Русское склонение существительного при числе: «1 участие», «2 участия», «5 участий».
 *
 * Нужно ровно там, где число подставляется в текст рядом со словом. Без склонения строка
 * читается как машинный вывод («от 1 участий»), а раздел, объясняющий пользователю, откуда
 * взялась цифра, теряет доверие именно на таких мелочах. */
export function plural(count: number, one: string, few: string, many: string): string {
  const mod100 = Math.abs(count) % 100;
  const mod10 = mod100 % 10;
  if (mod100 >= 11 && mod100 <= 14) return many;
  if (mod10 === 1) return one;
  if (mod10 >= 2 && mod10 <= 4) return few;
  return many;
}
