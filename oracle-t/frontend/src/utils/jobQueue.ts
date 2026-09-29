/**
 * Подписи очереди разборов (29.09.2026). У каждого пользователя разбирается одна закупка
 * за раз, остальные ждут; сервер разбирает несколько закупок разных людей одновременно.
 * Ожидающая задача хода не пишет — без этих подписей она выглядела бы как «модель
 * разбирает закупку» без единого признака жизни.
 */

function plural(count: number, one: string, few: string, many: string): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export function queueText(
  reason: "own" | "slot" | null | undefined,
  ahead: number | null | undefined,
): string | null {
  if (reason === "own" && ahead) {
    return `В очереди: сначала ${plural(ahead, "закончится", "закончатся", "закончатся")} ${ahead} ${plural(
      ahead,
      "ваш разбор",
      "ваших разбора",
      "ваших разборов",
    )}, запущенных раньше`;
  }
  if (reason === "slot") {
    return `В очереди: сервер занят разборами других пользователей (${ahead ?? 0}) — начнётся, как только один освободится`;
  }
  return null;
}

export const JOB_STATUS_LABELS: Record<string, string> = {
  queued: "в очереди",
  running: "идёт",
  success: "готово",
  error: "ошибка",
  cancelled: "остановлено",
};
