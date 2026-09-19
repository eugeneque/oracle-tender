import type { TagColor } from "../api/types";

/** Классы палитры по ключу цвета — единственное место, где ключ превращается в оттенок.
 * Ключи те же, что `TAG_COLORS` на бэкенде; неизвестный ключ красится как `zinc`. */
export const TAG_COLOR_CLASSES: Record<TagColor, { chip: string; dot: string }> = {
  zinc: { chip: "border-zinc-500/30 bg-zinc-500/10 text-zinc-300", dot: "bg-zinc-400" },
  red: { chip: "border-red-500/30 bg-red-500/10 text-red-300", dot: "bg-red-400" },
  orange: { chip: "border-orange-500/30 bg-orange-500/10 text-orange-300", dot: "bg-orange-400" },
  amber: { chip: "border-amber-500/30 bg-amber-500/10 text-amber-300", dot: "bg-amber-400" },
  emerald: { chip: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300", dot: "bg-emerald-400" },
  sky: { chip: "border-sky-500/30 bg-sky-500/10 text-sky-300", dot: "bg-sky-400" },
  indigo: { chip: "border-indigo-500/30 bg-indigo-500/10 text-indigo-300", dot: "bg-indigo-400" },
  violet: { chip: "border-violet-500/30 bg-violet-500/10 text-violet-300", dot: "bg-violet-400" },
  pink: { chip: "border-pink-500/30 bg-pink-500/10 text-pink-300", dot: "bg-pink-400" },
};

export function tagColorClasses(color: string): { chip: string; dot: string } {
  return TAG_COLOR_CLASSES[color as TagColor] ?? TAG_COLOR_CLASSES.zinc;
}
