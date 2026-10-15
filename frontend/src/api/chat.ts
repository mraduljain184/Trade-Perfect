import { apiClient } from '@/api/client';

export interface ChatResponse {
  answer: string;
  toolUsed?: string | null;
  data?: Record<string, unknown> | null;
}

export async function sendChatMessage(sessionId: string, message: string): Promise<ChatResponse> {
  const { data } = await apiClient.post<ChatResponse>('/chat', { sessionId, message });
  return data;
}
