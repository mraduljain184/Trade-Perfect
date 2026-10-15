import {
  AuditOutlined,
  CloudUploadOutlined,
  DashboardOutlined,
  FormOutlined,
  LineChartOutlined,
  MessageOutlined,
  ToolOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import type { ReactNode } from 'react';

import { resolveRole } from '@/auth/roles';
import type { RoleCode, User } from '@/types/api';

export interface NavItem {
  key: string;
  path: string;
  label: string;
  icon: ReactNode;
}

export const NAV: NavItem[] = [
  { key: 'book', path: '/trades/new', label: 'New trade', icon: <FormOutlined /> },
  { key: 'upload', path: '/trades/upload', label: 'Upload CSV', icon: <CloudUploadOutlined /> },
  { key: 'my', path: '/my-trades', label: 'My trades', icon: <UnorderedListOutlined /> },
  { key: 'dashboard', path: '/dashboard', label: 'Dashboard', icon: <DashboardOutlined /> },
  { key: 'queue', path: '/repair-queue', label: 'Repair queue', icon: <ToolOutlined /> },
  { key: 'audit', path: '/audit', label: 'Audit trail', icon: <AuditOutlined /> },
  { key: 'chat', path: '/chat', label: 'Settlement assistant', icon: <MessageOutlined /> },
  { key: 'ml', path: '/ml/monitoring', label: 'ML monitoring', icon: <LineChartOutlined /> },
];

const TRADER_KEYS = ['book', 'upload', 'my', 'queue', 'audit', 'chat'] as const;
/** Ops work the queue — they do not book trades in this app. */
const OPS_KEYS = ['dashboard', 'queue', 'audit', 'chat'] as const;
const ML_KEYS = ['ml', 'chat'] as const;

export function navForUser(user: User | null): NavItem[] {
  if (!user) return [];
  const role = resolveRole(user);
  switch (role) {
    case 'ML_ADMIN':
      return NAV.filter((i) => (ML_KEYS as readonly string[]).includes(i.key));
    case 'OPS_USER':
    case 'OPS_MANAGER':
      return NAV.filter((i) => (OPS_KEYS as readonly string[]).includes(i.key));
    case 'SYS_ADMIN':
      return NAV;
    case 'TRADER':
    default:
      return NAV.filter((i) => (TRADER_KEYS as readonly string[]).includes(i.key));
  }
}

export function showRepairQueueNav(user: User | null): boolean {
  return navForUser(user).some((i) => i.path === '/repair-queue');
}
