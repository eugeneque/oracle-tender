/** Форматирование чисел вкладки «Рынок ПУ». */

export const fmt = (value: number) => new Intl.NumberFormat("ru-RU").format(Math.round(value));

export function fmtShare(value: number): string {
  return `${value.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} %`;
}

export function quarterLabel(key: string): string {
  const [year, quarter] = key.split("-");
  return `${quarter} кв. ${year}`;
}
