/**
 * Разбор итога задачи для центра уведомлений (редизайн 23.10.2026).
 *
 * Полный разбор возвращает одну строку «Анализ: … | Матрица: … | Оценка: …», в которой
 * причина сбоя модели повторяется в каждом шаге («RouterAI отклонил ключ (HTTP 401: {…})»
 * трижды). Сплошным абзацем это не читалось. Здесь строка делится на шаги с коротким
 * итогом и тоном, а повторяющаяся причина выносится одной плашкой.
 */
export type SummaryTone = "ok" | "warn" | "error";

export interface SummaryStep {
  label: string;
  text: string;
  full: string;
  tone: SummaryTone;
}

export interface ActivitySummary {
  steps: SummaryStep[];
  /** Общая причина сбоя, если она одна на несколько шагов. */
  alert: string | null;
  tone: SummaryTone;
}

const ERROR_RE = /отклонил|ошибк|не удалось|http\s*\d{3}|не выполнен|ни один/i;
const WARN_RE = /не посчитан|не извлечен|прежняя оставлена|точность ниже|неразобранн/i;

function toneOf(text: string): SummaryTone {
  if (ERROR_RE.test(text)) return "error";
  if (WARN_RE.test(text)) return "warn";
  return "ok";
}

/** «RouterAI отклонил ключ (HTTP 401: {...}). Повтор не поможет — …» → без технического хвоста. */
function cleanCause(text: string): string {
  return text
    .replace(/\s*\(HTTP\s*\d{3}[^)]*\)\)?/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function shortText(text: string): string {
  // Первая часть до «;» — итог шага, дальше — пояснения.
  let first = cleanCause(text.split(";")[0]);
  // «не посчитана: Оценка не посчитана, прежняя оставлена…» — суть в первых словах.
  if (/^не\s/i.test(first) && first.includes(":")) first = first.slice(0, first.indexOf(":"));
  first = first.charAt(0).toUpperCase() + first.slice(1);
  return first.length > 90 ? `${first.slice(0, 88)}…` : first;
}

function findCause(text: string): string | null {
  const match = /([A-ZА-ЯЁ][\wА-Яа-яЁё-]*\s+отклонил[^.(;]*)(?:\s*\([^)]*\)\)?)?\.?(\s*Повтор[^;|]*)?/.exec(text);
  if (!match) return null;
  return cleanCause(`${match[1].trim()}.${match[2] ?? ""}`);
}

export function summarizeActivity(message: string | null): ActivitySummary | null {
  if (!message || !message.includes(" | ")) return null;
  const steps = message.split(" | ").map((part) => {
    const index = part.indexOf(":");
    const label = index > 0 && index < 24 ? part.slice(0, index).trim() : "";
    const body = (label ? part.slice(index + 1) : part).trim();
    return { label, text: shortText(body), full: body, tone: toneOf(body) };
  });
  const alert = findCause(message);
  const tone: SummaryTone = steps.some((s) => s.tone === "error")
    ? "error"
    : steps.some((s) => s.tone === "warn")
      ? "warn"
      : "ok";
  return { steps, alert, tone };
}
