import type { AxiosError } from 'axios';

import type { ApiErrorBody, ApiErrorDetail } from '@/types/api';

export function normalizeApiError(err: unknown): ApiErrorDetail {
  const ax = err as AxiosError<ApiErrorBody>;
  if (ax.response?.data?.error) {
    return ax.response.data.error;
  }
  return {
    code: 'NETWORK_ERROR',
    message: ax.message || 'Unable to reach the server. Is the API running?',
  };
}
