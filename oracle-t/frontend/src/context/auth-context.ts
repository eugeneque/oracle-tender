import { createContext } from "react";

import type { User } from "../api/types";

export interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

// Сам контекст вынесен из `AuthContext.tsx` в отдельный модуль: Fast Refresh в Vite работает
// только когда файл экспортирует одни компоненты, а провайдер соседствовал с контекстом и хуком.
export const AuthContext = createContext<AuthContextValue | undefined>(undefined);
