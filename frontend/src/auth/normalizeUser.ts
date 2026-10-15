import { resolveRole } from '@/auth/roles';
import type { User } from '@/types/api';

export function normalizeUser(user: User): User {
  return {
    ...user,
    role: resolveRole(user),
    permissions: Array.isArray(user.permissions) ? user.permissions : [],
  };
}
