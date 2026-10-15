import { apiClient } from '@/api/client';
import type { UploadBatch } from '@/types/api';

export async function uploadBulkCsv(file: File): Promise<{ batchId: string }> {
  const form = new FormData();
  form.append('file', file);
  const { data } = await apiClient.post<{ batchId: string }>('/trades/bulk', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return data;
}

export async function getUploadBatch(batchId: string): Promise<UploadBatch> {
  const { data } = await apiClient.get<UploadBatch>(`/uploads/${batchId}`);
  return data;
}
