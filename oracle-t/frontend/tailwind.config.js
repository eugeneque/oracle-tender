import defaultColors from "tailwindcss/colors";
import plugin from "tailwindcss/plugin";

/*
 * Тёмная и светлая темы (28.09.2026).
 *
 * Интерфейс писался под тёмный фон: «белый» в классах означает «передний план» (текст,
 * тонкие рамки, подложки `bg-white/5`), а светлые оттенки палитр (`text-indigo-300`) —
 * акцентный текст. Поэтому светлая тема не переписывает компоненты, а зеркалит шкалы:
 * каждый цвет берётся из CSS-переменной, и в `[data-theme="light"]` оттенок 300 получает
 * значение 700, 100 — 900 и т. д., а `white` и `black` меняются местами. Всё, что должно
 * остаться белым в обеих темах (текст на цветной кнопке), пишется через `snow`, а
 * затемнение под модальным окном — через `scrim`.
 */

const SHADES = ["50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"];
const HUES = [
  "slate", "gray", "zinc", "neutral", "stone", "red", "orange", "amber", "yellow", "lime",
  "green", "emerald", "teal", "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia",
  "pink", "rose",
];

// Серая шкала в светлой теме подобрана вручную, а не простым зеркалом: панели (`zinc-900`)
// должны стать белыми, а фон страницы (`zinc-950`) — светло-серым, как в macOS.
const LIGHT_ZINC = {
  50: "#09090b",
  100: "#18181b",
  200: "#27272a",
  300: "#3f3f46",
  400: "#52525b",
  500: "#71717a",
  600: "#a1a1aa",
  700: "#d4d4d8",
  800: "#e4e4e7",
  900: "#ffffff",
  950: "#f4f4f5",
};

function rgbChannels(hex) {
  const value = hex.replace("#", "");
  const full = value.length === 3 ? value.split("").map((c) => c + c).join("") : value;
  const n = parseInt(full, 16);
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`;
}

const varColor = (name) => `rgb(var(--c-${name}) / <alpha-value>)`;

const colors = {
  transparent: "transparent",
  current: "currentColor",
  inherit: "inherit",
  white: varColor("white"),
  black: varColor("black"),
  snow: "#ffffff",
  scrim: "#000000",
};
for (const hue of HUES) {
  colors[hue] = Object.fromEntries(SHADES.map((s) => [s, varColor(`${hue}-${s}`)]));
}

const darkVars = { "--c-white": "255 255 255", "--c-black": "0 0 0" };
const lightVars = { "--c-white": "9 9 11", "--c-black": "255 255 255" };
for (const hue of HUES) {
  SHADES.forEach((shade, i) => {
    darkVars[`--c-${hue}-${shade}`] = rgbChannels(defaultColors[hue][shade]);
    const mirrored = SHADES[SHADES.length - 1 - i];
    lightVars[`--c-${hue}-${shade}`] = rgbChannels(
      hue === "zinc" ? LIGHT_ZINC[shade] : defaultColors[hue][mirrored],
    );
  });
}

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    colors,
    extend: {},
  },
  plugins: [
    plugin(({ addBase }) => {
      addBase({
        ":root": { ...darkVars, colorScheme: "dark" },
        ':root[data-theme="light"]': { ...lightVars, colorScheme: "light" },
      });
    }),
  ],
};
