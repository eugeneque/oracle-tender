import { useEffect, useState } from "react";

import { fetchImageUrl } from "../api/client";
import type { User } from "../api/types";

export function initials(fullName: string | undefined): string {
  if (!fullName) return "?";
  const parts = fullName.trim().split(/\s+/);
  return parts
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("");
}

/**
 * Object URL аватара пользователя (замечание 17.09.2026). Байты закрыты токеном, поэтому
 * картинка забирается fetch-ом, а не `<img src="/api/...">`. Ключ перечитывания —
 * `avatar_updated_at`: сменил аватар — метка изменилась — картинка загрузилась заново.
 */
export function useAvatarUrl(user: Pick<User, "id" | "avatar_updated_at"> | null): string | null {
  const [url, setUrl] = useState<string | null>(null);
  const userId = user?.id ?? null;
  const version = user?.avatar_updated_at ?? null;

  useEffect(() => {
    if (!userId || !version) {
      setUrl(null);
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    void fetchImageUrl(`/users/${userId}/avatar`).then((loaded) => {
      if (cancelled) {
        if (loaded) URL.revokeObjectURL(loaded);
        return;
      }
      objectUrl = loaded;
      setUrl(loaded);
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [userId, version]);

  return url;
}
