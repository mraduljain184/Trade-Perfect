import { apiClient } from '@/api/client';
import type { Paged, TradeInput, TradeResult, ValidationResult } from '@/types/api';

export async function validateTrade(body: TradeInput): Promise<ValidationResult> {
  const { data } = await apiClient.post<ValidationResult>('/trades/validate', body);
  return data;
}

export async function createTrade(body: TradeInput): Promise<TradeResult> {
  const { data } = await apiClient.post<TradeResult>('/trades', body);
  return data;
}

export interface TradeListParams {
  page?: number;
  pageSize?: number;
  mine?: boolean;
  status?: string;
  search?: string;
  sort?: string;
}

export async function listTrades(params: TradeListParams = {}): Promise<Paged<TradeResult>> {
  const { data } = await apiClient.get<Paged<TradeResult>>('/trades', { params });
  return data;
}

export async function exportMyTrades(mine = true): Promise<Blob> {
  const { data } = await apiClient.get<Blob>('/trades/export', {
    params: { mine },
    responseType: 'blob',
  });
  return data;
}

export async function getTrade(tradeId: string) {
  const { data } = await apiClient.get<import('@/types/api').TradeDetail>(`/trades/${tradeId}`);
  return data;
}

export async function getTradeHistory(tradeId: string) {
  const { data } = await apiClient.get<import('@/types/api').TradeVersion[]>(`/trades/${tradeId}/history`);
  return data;
}

export async function amendTrade(tradeId: string, body: TradeInput & { queueId?: string; appliedFix?: string; changeReason?: string }) {
  const { data } = await apiClient.put<TradeResult>(`/trades/${tradeId}`, body);
  return data;
}
