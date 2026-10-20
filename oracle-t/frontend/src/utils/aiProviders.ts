import type { AiProviderKey } from "../api/types";

/** Провайдеры ИИ-модуля в порядке показа в переключателях. */
export const AI_PROVIDERS: { key: AiProviderKey; label: string; hint: string }[] = [
  { key: "yandex", label: "YandexGPT", hint: "Yandex AI Studio" },
  { key: "deepseek", label: "DeepSeek", hint: "через RouterAI" },
  { key: "qwen", label: "Qwen", hint: "Qwen3.8 27B · через RouterAI" },
];

/**
 * Акцент провайдера в интерфейсе: индиго — YandexGPT, синий — DeepSeek (цвет его знака),
 * фиолетовый — Qwen (цвет его знака, 20.10.2026). По цвету пользователь видит, какая модель писала разбор, поэтому классы
 * собраны в одном месте, а не угадываются в каждом компоненте.
 */
export const AI_PROVIDER_ACCENT: Record<
  AiProviderKey,
  {
    /** Выбранная кнопка переключателя. */
    segment: string;
    /** Карточка провайдера — модель по умолчанию. */
    card: string;
    /** Плашка «по умолчанию». */
    pill: string;
    /** Бейдж модели в шапке «Разбора ИИ». */
    badge: string;
    /** Рамка блока «Разбор ИИ» и полосы-волны в его шапке (`""` — родная гамма). */
    panel: string;
    wave: string;
    /** Кнопка запуска разбора и строка «идёт пересчёт». */
    button: string;
    text: string;
  }
> = {
  yandex: {
    segment: "bg-indigo-500/20 text-indigo-100 shadow-[inset_0_0_0_1px_rgba(129,140,248,0.45)]",
    card: "border-indigo-400/30 bg-indigo-500/[0.04]",
    pill: "bg-indigo-500/15 text-indigo-200",
    badge: "border-white/10 bg-white/5 text-zinc-300 hover:bg-white/10",
    panel: "border-white/[0.1]",
    wave: "border-white/[0.08]",
    button: "border-white/15 bg-white/5 hover:bg-white/10",
    text: "text-indigo-300",
  },
  deepseek: {
    segment: "bg-blue-500/20 text-blue-100 shadow-[inset_0_0_0_1px_rgba(96,165,250,0.45)]",
    card: "border-blue-400/30 bg-blue-500/[0.04]",
    pill: "bg-blue-500/15 text-blue-200",
    badge: "border-blue-400/30 bg-blue-500/10 text-blue-200 hover:bg-blue-500/20",
    panel: "border-blue-400/25",
    wave: "ai-wave--deepseek border-blue-400/15",
    button: "border-blue-400/30 bg-blue-500/10 hover:bg-blue-500/20",
    text: "text-blue-300",
  },
  qwen: {
    segment: "bg-violet-500/20 text-violet-100 shadow-[inset_0_0_0_1px_rgba(167,139,250,0.45)]",
    card: "border-violet-400/30 bg-violet-500/[0.04]",
    pill: "bg-violet-500/15 text-violet-200",
    badge: "border-violet-400/30 bg-violet-500/10 text-violet-200 hover:bg-violet-500/20",
    panel: "border-violet-400/25",
    wave: "ai-wave--qwen border-violet-400/15",
    button: "border-violet-400/30 bg-violet-500/10 hover:bg-violet-500/20",
    text: "text-violet-300",
  },
};
