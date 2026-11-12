import { useEffect, useState } from "react";

import { api } from "../api/client";

/**
 * Закупки с изменениями, которых пользователь ещё не видел (12.11.2026, раздел 100
 * ARCHITECTURE.md): id → число событий. Зелёная точка в строке списка гаснет, когда журнал
 * событий закупки открыт (событие окна `tender-events-seen` из карточки).
 *
 * Перечитывается раз в пять минут: проверка закупок идёт раз в полчаса, чаще незачем.
 */
const REFRESH_MS = 5 * 60 * 1000;

export function useUnseenEvents(): Record<string, number> {
  const [unseen, setUnseen] = useState<Record<string, number>>({});

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api
        .get<Record<string, number>>("/monitoring/unseen")
        .then((data) => {
          if (!cancelled) setUnseen(data);
        })
        .catch(() => undefined);
    void load();
    const timer = window.setInterval(load, REFRESH_MS);
    const onSeen = (event: Event) => {
      const id = (event as CustomEvent<string>).detail;
      setUnseen((current) => {
        if (!(id in current)) return current;
        const next = { ...current };
        delete next[id];
        return next;
      });
    };
    window.addEventListener("tender-events-seen", onSeen);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("tender-events-seen", onSeen);
    };
  }, []);

  return unseen;
}
