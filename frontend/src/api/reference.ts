import { apiClient } from '@/api/client';
import type { AccountRef, CounterpartyRef, SymbolRef } from '@/types/api';

export async function fetchSymbols(): Promise<SymbolRef[]> {
  const { data } = await apiClient.get<SymbolRef[]>('/reference/symbols');
  return data;
}

export async function fetchCounterparties(): Promise<CounterpartyRef[]> {
  const { data } = await apiClient.get<CounterpartyRef[]>('/reference/counterparties');
  return data;
}

export async function fetchAccounts(): Promise<AccountRef[]> {
  const { data } = await apiClient.get<AccountRef[]>('/reference/accounts');
  return data;
}

export async function fetchUsers(role?: string) {
  const { data } = await apiClient.get<import('@/types/api').UserBrief[]>('/reference/users', {
    params: role ? { role } : undefined,
  });
  return data;
}
