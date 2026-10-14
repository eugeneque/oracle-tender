import { useContext } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

import { PageHeaderSlotContext } from "./pageHeaderSlotContext";

/**
 * Место в глобальной шапке, куда страница может поставить свой элемент — сейчас поиск
 * на «Тендерах» (14.10.2026). Раньше поиск стоял в шапке самой страницы и забирал у
 * фильтров целую строку, а верхняя полоса приложения была почти пустой: справа индикатор
 * задач и аватар. Шапка — часть каркаса (`AppLayout`), страница — внутри `<Outlet>`,
 * поэтому элемент переносится порталом в узел, который каркас отдаёт через контекст.
 */
export function PageHeaderPortal({ children }: { children: ReactNode }) {
  const slot = useContext(PageHeaderSlotContext);
  return slot ? createPortal(children, slot) : null;
}
