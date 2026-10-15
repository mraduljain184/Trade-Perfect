import { defaultRouteForRole } from '@/auth/defaultRoute';
import type { RoleCode } from '@/types/api';

const APP_PATHS = ['/trades/new', '/trades/upload', '/my-trades', '/dashboard', '/repair-queue', '/audit', '/chat', '/ml/monitoring'];

/** After login, send the user to their role home — not back to `/` or login. */
export function postLoginPath(role: RoleCode, from?: string | null): string {
  const home = defaultRouteForRole(role);
  if (!from || from === '/' || from === '/login') return home;
  if (APP_PATHS.some((p) => from === p || from.startsWith(`${p}/`))) {
    return from;
  }
  return home;
}
