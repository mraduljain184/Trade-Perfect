import { apiClient } from '@/api/client';
import type { LoginRequest, LoginResponse, User } from '@/types/api';

export async function loginRequest(body: LoginRequest): Promise<LoginResponse> {
  const { data } = await apiClient.post<LoginResponse>('/auth/login', body);
  return data;
}

export async function fetchMe(): Promise<User> {
  const { data } = await apiClient.get<User>('/auth/me');
  return data;
}

export async function logoutRequest(): Promise<void> {
  await apiClient.post('/auth/logout');
}
