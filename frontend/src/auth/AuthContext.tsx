import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { fetchMe, loginRequest, logoutRequest } from '@/api/auth';
import { setUnauthorizedHandler } from '@/api/client';
import { clearStoredSession, loadStoredSession, saveStoredSession } from '@/api/storage';
import { normalizeUser } from '@/auth/normalizeUser';
import type { User } from '@/types/api';

interface AuthState {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  hasPermission: (code: string) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const clearSession = useCallback(() => {
    clearStoredSession();
    setToken(null);
    setUser(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearSession();
      window.location.assign('/login?expired=1');
    });
  }, [clearSession]);

  useEffect(() => {
    const stored = loadStoredSession();
    if (!stored) {
      setLoading(false);
      return;
    }
    setToken(stored.token);
    fetchMe()
      .then((me) => {
        const normalized = normalizeUser(me);
        setUser(normalized);
        saveStoredSession(stored.token, normalized);
      })
      .catch(() => clearSession())
      .finally(() => setLoading(false));
  }, [clearSession]);

  const login = useCallback(async (username: string, password: string) => {
    const res = await loginRequest({ username: username.trim(), password });
    const normalized = normalizeUser(res.user);
    saveStoredSession(res.accessToken, normalized);
    setToken(res.accessToken);
    setUser(normalized);
    return normalized;
  }, []);

  const logout = useCallback(async () => {
    try {
      if (token) await logoutRequest();
    } catch {
      /* still clear local session */
    }
    clearSession();
  }, [clearSession, token]);

  const hasPermission = useCallback(
    (code: string) => user?.permissions.includes(code) ?? false,
    [user],
  );

  const value = useMemo(
    () => ({ user, token, loading, login, logout, hasPermission }),
    [user, token, loading, login, logout, hasPermission],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
