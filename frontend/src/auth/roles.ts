import type { RoleCode, User } from '@/types/api';

export function resolveRole(user: User): RoleCode {
  const code = String(user.role ?? '').toUpperCase();
  if (code === 'TRADER' || code === 'OPS_USER' || code === 'OPS_MANAGER' || code === 'ML_ADMIN' || code === 'SYS_ADMIN') {
    return code as RoleCode;
  }
  const name = (user.roleName ?? '').toLowerCase();
  if (name.includes('manager')) return 'OPS_MANAGER';
  if (name.includes('operations')) return 'OPS_USER';
  if (name.includes('ml') || name.includes('model')) return 'ML_ADMIN';
  if (name.includes('system') && name.includes('admin')) return 'SYS_ADMIN';
  return 'TRADER';
}

export function isTrader(user: User | null): boolean {
  return user != null && resolveRole(user) === 'TRADER';
}

export function isOps(user: User | null): boolean {
  if (!user) return false;
  const r = resolveRole(user);
  return r === 'OPS_USER' || r === 'OPS_MANAGER';
}
