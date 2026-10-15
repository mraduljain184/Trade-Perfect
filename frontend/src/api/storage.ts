import type { User } from '@/types/api';

const TOKEN_KEY = 'tradeperfect.accessToken';
const USER_KEY = 'tradeperfect.user';

export function loadStoredSession(): { token: string; user: User } | null {
  const token = sessionStorage.getItem(TOKEN_KEY);
  const raw = sessionStorage.getItem(USER_KEY);
  if (!token || !raw) return null;
  try {
    return { token, user: JSON.parse(raw) as User };
  } catch {
    clearStoredSession();
    return null;
  }
}

export function saveStoredSession(token: string, user: User): void {
  sessionStorage.setItem(TOKEN_KEY, token);
  sessionStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearStoredSession(): void {
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(USER_KEY);
}

export function getStoredToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}
