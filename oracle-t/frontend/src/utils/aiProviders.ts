import type { AiProviderKey } from "../api/types";

/** Провайдеры ИИ-модуля в порядке показа в переключателях. */
export const AI_PROVIDERS: { key: AiProviderKey; label: string; hint: string }[] = [
  { key: "yandex", label: "YandexGPT", hint: "Yandex AI Studio" },
  { key: "claude", label: "Claude", hint: "через RouterAI" },
  { key: "deepseek", label: "DeepSeek", hint: "через RouterAI" },
  { key: "gigachat", label: "GigaChat", hint: "Сбер" },
];

/**
 * Акцент провайдера в интерфейсе: индиго — YandexGPT, оранжевый — Claude, синий — DeepSeek
 * (цвет его знака), зелёный — GigaChat (цвет Сбера). По цвету пользователь видит, какая модель писала разбор, поэтому классы
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
  claude: {
    segment: "bg-orange-500/20 text-orange-100 shadow-[inset_0_0_0_1px_rgba(251,146,60,0.45)]",
    card: "border-orange-400/30 bg-orange-500/[0.04]",
    pill: "bg-orange-500/15 text-orange-200",
    badge: "border-orange-400/30 bg-orange-500/10 text-orange-200 hover:bg-orange-500/20",
    panel: "border-orange-400/25",
    wave: "ai-wave--claude border-orange-400/15",
    button: "border-orange-400/30 bg-orange-500/10 hover:bg-orange-500/20",
    text: "text-orange-300",
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
  gigachat: {
    segment: "bg-emerald-500/20 text-emerald-100 shadow-[inset_0_0_0_1px_rgba(52,211,153,0.45)]",
    card: "border-emerald-400/30 bg-emerald-500/[0.04]",
    pill: "bg-emerald-500/15 text-emerald-200",
    badge: "border-emerald-400/30 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20",
    panel: "border-emerald-400/25",
    wave: "ai-wave--gigachat border-emerald-400/15",
    button: "border-emerald-400/30 bg-emerald-500/10 hover:bg-emerald-500/20",
    text: "text-emerald-300",
  },
};
