import type { Transition, Variants } from "motion/react";

// Общий «язык движения» интерфейса (28.09.2026), те же кривые и пружины, что у smoothui в
// проекте wise: у каждого типа элемента своя анимация, чтобы экраны двигались одинаково.
// Все анимации уважают `prefers-reduced-motion` — см. <MotionConfig reducedMotion="user"> в main.tsx.

export const EASE_OUT = [0.23, 1, 0.32, 1] as const;

/** Подсветка активного пункта, ползунок переключателя: быстро и без перелёта. */
export const SPRING_SNAPPY: Transition = { type: "spring", bounce: 0, duration: 0.3 };
/** Панели и выпадающие меню: мягкая пружина с едва заметной отдачей. */
export const SPRING_SOFT: Transition = { type: "spring", bounce: 0.12, duration: 0.4 };
export const SPRING_POP: Transition = { type: "spring", stiffness: 520, damping: 28 };

/**
 * Смена страницы: лёгкий подъём с расфокусом. `filter` снимается по окончании — иначе он
 * становится containing block для `position: fixed` внутри страницы (модальные окна).
 */
export const pageVariants: Variants = {
  initial: { opacity: 0, y: 10, filter: "blur(4px)" },
  animate: {
    opacity: 1,
    y: 0,
    filter: "blur(0px)",
    transition: { duration: 0.35, ease: EASE_OUT },
    transitionEnd: { filter: "none" },
  },
};

/** Подложка модального окна. */
export const scrimVariants: Variants = {
  initial: { opacity: 0 },
  animate: { opacity: 1, transition: { duration: 0.2 } },
  exit: { opacity: 0, transition: { duration: 0.15 } },
};

/** Модальное окно: всплывает из глубины. */
export const dialogVariants: Variants = {
  initial: { opacity: 0, scale: 0.96, y: 12 },
  animate: { opacity: 1, scale: 1, y: 0, transition: SPRING_SOFT },
  exit: { opacity: 0, scale: 0.97, y: 6, transition: { duration: 0.15 } },
};

/** Выпадающее меню: раскрывается от своей кнопки. */
export const dropdownVariants: Variants = {
  initial: { opacity: 0, y: -6, scale: 0.97, filter: "blur(4px)" },
  animate: { opacity: 1, y: 0, scale: 1, filter: "blur(0px)", transition: SPRING_SOFT },
  exit: { opacity: 0, y: -4, scale: 0.98, filter: "blur(2px)", transition: { duration: 0.12 } },
};

/** Группы пунктов: каскад сверху вниз. */
export const staggerContainer: Variants = {
  initial: {},
  animate: { transition: { staggerChildren: 0.035, delayChildren: 0.04 } },
};
export const staggerItem: Variants = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0, transition: { duration: 0.3, ease: EASE_OUT } },
};

/** Навигация бокового меню: пункты выезжают слева. */
export const navItemVariants: Variants = {
  initial: { opacity: 0, x: -10 },
  animate: { opacity: 1, x: 0, transition: SPRING_SOFT },
};
