import type { RoleCode } from '@/types/api';

/** PROJECT_CONTEXT §13.1 default landing after login */
export function defaultRouteForRole(role: RoleCode): string {
  switch (role) {
    case 'TRADER':
      return '/trades/new';
    case 'ML_ADMIN':
      return '/ml/monitoring';
    case 'OPS_USER':
    case 'OPS_MANAGER':
      return '/repair-queue';
    case 'SYS_ADMIN':
      return '/dashboard';
    default:
      return '/home';
  }
}
