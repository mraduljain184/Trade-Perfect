import { Tag } from 'antd';

import type { TradeStatus } from '@/types/api';

const COLORS: Record<TradeStatus, string> = {
  PASS: 'success',
  MONITOR: 'warning',
  EXCEPTION: 'error',
  REJECTED: 'default',
};

export function StatusTag({ status }: { status: TradeStatus | string }) {
  const s = status as TradeStatus;
  return <Tag color={COLORS[s] ?? 'default'}>{status}</Tag>;
}
