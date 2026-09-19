import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { AiProviderKey, AiProviderStatus } from "../api/types";

/**
 * Модель ИИ текущего пользователя — общая для всего интерфейса.
 *
 * Значение одно на пользователя и меняется редко (щелчком в карточке тендера или в учётной
 * записи), а читается из многих мест: каждая открытая карточка тендера красит блок
 * «Разбор ИИ» под модель. Поэтому запрос делается один раз на вкладку и кэшируется на
 * уровне модуля, а не в каждом компоненте; после переключения `setAiProvider` рассылает
 * новое значение всем подписчикам — карточка перекрашивается без перезагрузки.
 *
 * Пока ответ не пришёл (или сервер недоступен) — `null`: блок рисуется в нейтральной
 * раскраске Yandex, как до появления переключателя.
 */

let cached: AiProviderStatus | null = null;
let inflight: Promise<void> | null = null;
const listeners = new Set<(status: AiProviderStatus | null) => void>();

function notify() {
  listeners.forEach((listener) => listener(cached));
}

export function setAiProvider(status: AiProviderStatus) {
  cached = status;
  notify();
}

/** Личный выбор модели: `null` — вернуться к системной по умолчанию. Обновляет кэш и всех
 * подписчиков; ошибку (например, «модель не настроена») отдаёт вызывающему. */
export async function chooseMyAiProvider(
  provider: AiProviderKey | null,
): Promise<AiProviderStatus> {
  const next = await api.put<AiProviderStatus>("/auth/me/ai-provider", {
    ai_provider: provider,
  });
  setAiProvider(next);
  return next;
}

/** Смена системной модели по умолчанию (администратор). Ответ — статус для самого
 * администратора, поэтому кэш обновляется им же. */
export async function chooseDefaultAiProvider(
  provider: AiProviderKey,
): Promise<AiProviderStatus> {
  const next = await api.put<AiProviderStatus>("/integrations/ai-provider", {
    active_provider: provider,
  });
  setAiProvider(next);
  return next;
}

export function refreshAiProvider(): Promise<void> {
  if (!inflight) {
    inflight = api
      .get<AiProviderStatus>("/integrations/ai-provider")
      .then((status) => {
        cached = status;
        notify();
      })
      .catch(() => {
        // Не настроено или нет сети — карточка остаётся в нейтральной раскраске.
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

export function useAiProvider(): AiProviderStatus | null {
  const [status, setStatus] = useState<AiProviderStatus | null>(cached);

  useEffect(() => {
    listeners.add(setStatus);
    if (cached === null) {
      void refreshAiProvider();
    } else {
      setStatus(cached);
    }
    return () => {
      listeners.delete(setStatus);
    };
  }, []);

  return status;
}
