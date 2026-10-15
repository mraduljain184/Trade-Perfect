import { apiClient } from '@/api/client';
import type { TradeInput } from '@/types/api';

/** Score one trade with rules + ML — nothing saved (PROJECT_CONTEXT §8.1 #47). */
export interface PredictResult {
  tradeId: string | null;
  status: string;
  priority: string | null;
  ruleScore: number | null;
  mlScore: number | null;
  finalScore: number;
  likelyReason: string | null;
  suggestedAction: string | null;
  explanation: string | null;
  rejectionReasons: { ruleId: string; field?: string; message: string }[];
  modelVersion: string | null;
  businessDate: string;
  saved: boolean;
}

export async function predictTrade(body: TradeInput): Promise<PredictResult> {
  const { data } = await apiClient.post<PredictResult>('/ml/predict', body);
  return data;
}
