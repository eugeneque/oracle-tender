import { createContext } from "react";

/** Узел в шапке приложения для элемента страницы — см. PageHeaderSlot.tsx. */
export const PageHeaderSlotContext = createContext<HTMLElement | null>(null);
