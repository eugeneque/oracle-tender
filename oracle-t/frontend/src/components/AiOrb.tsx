import type { AiProviderKey } from "../api/types";

/**
 * Светящаяся сфера ИИ — визуальный маркер того, что содержимое блока сгенерировала модель.
 *
 * Чистый CSS, без библиотек и без картинок: три размытых цветных пятна вращаются с разной
 * скоростью и в разные стороны внутри круга с маской, сверху — тонкое световое кольцо. Так
 * получается «переливание» без единого кадра анимации и без нагрузки на процессор от
 * canvas/WebGL, а в списке из десятков карточек это заметно.
 *
 * `prefers-reduced-motion` останавливает вращение: непрерывное движение на экране —
 * известная проблема для части пользователей, и системная настройка на этот счёт есть.
 *
 * `variant` — гамма под активного провайдера: розово-голубая у YandexGPT, оранжевая у
 * Claude, синяя у DeepSeek, зелёная у GigaChat (см. `.ai-orb--claude`, `.ai-orb--deepseek`,
 * `.ai-orb--gigachat` в index.css).
 * Меняются только цвета пятен, не геометрия.
 */
export function AiOrb({
  size = 44,
  busy = false,
  variant = "yandex",
}: {
  size?: number;
  busy?: boolean;
  variant?: AiProviderKey;
}) {
  return (
    <div
      className={variant === "yandex" ? "ai-orb" : `ai-orb ai-orb--${variant}`}
      style={{ width: size, height: size }}
      data-busy={busy ? "true" : undefined}
      aria-hidden="true"
    >
      <span className="ai-orb__blob ai-orb__blob--pink" />
      <span className="ai-orb__blob ai-orb__blob--blue" />
      <span className="ai-orb__blob ai-orb__blob--violet" />
      <span className="ai-orb__ring" />
    </div>
  );
}
