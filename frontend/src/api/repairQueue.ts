import { apiClient } from '@/api/client';
import type { BulkResult, Paged, QueueItem, QueueSummary } from '@/types/api';

export interface QueueListParams {
  page?: number;
  pageSize?: number;
  status?: string;
  priority?: string;
  assignedTo?: string;
  search?: string;
  sort?: string;
  minRiskScore?: number;
}

export async function getQueueSummary(params: Pick<QueueListParams, 'minRiskScore'> = {}): Promise<QueueSummary> {
  const { data } = await apiClient.get<QueueSummary>('/repair-queue/summary', { params });
  return data;
}

export async function listQueueItems(params: QueueListParams = {}): Promise<Paged<QueueItem>> {
  const { data } = await apiClient.get<Paged<QueueItem>>('/repair-queue', { params });
  return data;
}

export async function getQueueItem(queueId: string): Promise<QueueItem> {
  const { data } = await apiClient.get<QueueItem>(`/repair-queue/${queueId}`);
  return data;
}

export async function assignQueue(queueIds: string[], assigneeId: number | null): Promise<BulkResult> {
  const { data } = await apiClient.post<BulkResult>('/repair-queue/assign', { queueIds, assigneeId });
  return data;
}

export async function resubmitQueueItem(queueId: string): Promise<QueueItem> {
  const { data } = await apiClient.post<QueueItem>(`/repair-queue/${queueId}/resubmit`);
  return data;
}

export async function rejectQueueItem(queueId: string, reason: string): Promise<QueueItem> {
  const { data } = await apiClient.post<QueueItem>(`/repair-queue/${queueId}/reject`, { reason });
  return data;
}

export async function approveQueueItem(queueId: string, note: string): Promise<QueueItem> {
  const { data } = await apiClient.post<QueueItem>(`/repair-queue/${queueId}/approve`, { note });
  return data;
}

export async function bulkResubmit(queueIds: string[]): Promise<BulkResult> {
  const { data } = await apiClient.post<BulkResult>('/repair-queue/bulk-resubmit', { queueIds });
  return data;
}
