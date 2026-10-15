import { useQuery } from '@tanstack/react-query';

import { fetchAccounts, fetchCounterparties, fetchSymbols } from '@/api/reference';

export function useReferenceData() {
  const symbols = useQuery({ queryKey: ['symbols'], queryFn: fetchSymbols, staleTime: 5 * 60_000 });
  const counterparties = useQuery({
    queryKey: ['counterparties'],
    queryFn: fetchCounterparties,
    staleTime: 5 * 60_000,
  });
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: fetchAccounts, staleTime: 5 * 60_000 });
  return { symbols, counterparties, accounts };
}
