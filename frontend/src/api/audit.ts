import { apiClient } from '@/api/client';
import type { AuditBox, AuditEvent, Paged } from '@/types/api';

export interface AuditSearchParams {
  page?: number;
  pageSize?: number;
  tradeId?: string;
  dateFrom?: string;
  dateTo?: string;
  dateField?: 'tradeDate' | 'settlementDate' | 'eventTime';
  userId?: number;
  action?: string;
  search?: string;
}

export async function searchAudit(params: AuditSearchParams = {}): Promise<Paged<AuditEvent>> {
  const { data } = await apiClient.get<Paged<AuditEvent>>('/audit', { params });
  return data;
}

export async function getTradeTimeline(tradeId: string): Promise<AuditEvent[]> {
  const { data } = await apiClient.get<AuditEvent[]>(`/audit/trades/${tradeId}/timeline`);
  return data;
}

export async function getTradeAuditBox(tradeId: string): Promise<AuditBox> {
  const { data } = await apiClient.get<AuditBox>(`/audit/trades/${tradeId}/latest`);
  return data;
}

export async function verifyAuditChain(): Promise<{ valid: boolean; brokenAtId: number | null }> {
  const { data } = await apiClient.get<{ valid: boolean; brokenAtId: number | null }>('/audit/verify');
  return data;
}
