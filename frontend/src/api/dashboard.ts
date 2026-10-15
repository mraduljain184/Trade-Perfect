import { apiClient } from '@/api/client';
import type { DashboardSummary } from '@/types/api';

export async function getDashboardSummary(date?: string): Promise<DashboardSummary> {
  const { data } = await apiClient.get<DashboardSummary>('/dashboard/summary', {
    params: date ? { date } : undefined,
  });
  return data;
}
