import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";

import { api, getToken, setToken } from "../api/client";
import type { TokenResponse, User } from "../api/types";
import { AuthContext } from "./auth-context";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const loadCurrentUser = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setIsLoading(false);
      return;
    }
    try {
      const me = await api.get<User>("/auth/me");
      setUser(me);
    } catch {
      setToken(null);
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCurrentUser();
  }, [loadCurrentUser]);

  const login = useCallback(async (username: string, password: string) => {
    const tokenResponse = await api.post<TokenResponse>("/auth/login", { username, password });
    setToken(tokenResponse.access_token);
    const me = await api.get<User>("/auth/me");
    setUser(me);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setToken(null);
      setUser(null);
    }
  }, []);

  const updateUser = useCallback((updated: User) => setUser(updated), []);

  return (
    <AuthContext.Provider value={{ user, isLoading, login, logout, updateUser }}>
      {children}
    </AuthContext.Provider>
  );
}
