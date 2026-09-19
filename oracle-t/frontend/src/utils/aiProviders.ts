import type { AiProviderKey } from "../api/types";

/** Провайдеры ИИ-модуля в порядке показа в переключателях. */
export const AI_PROVIDERS: { key: AiProviderKey; label: string; hint: string }[] = [
  { key: "yandex", label: "YandexGPT", hint: "Yandex AI Studio" },
  { key: "claude", label: "Claude", hint: "через RouterAI" },
];
