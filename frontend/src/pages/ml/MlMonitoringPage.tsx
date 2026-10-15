import { Card, Descriptions, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';

import { apiClient } from '@/api/client';
import { useAuth } from '@/auth/AuthContext';

interface HybridConfig {
  alpha: number;
  beta: number;
  passThreshold?: number;
  exceptionThreshold?: number;
  psiThreshold?: number;
  pass_threshold?: number;
  exception_threshold?: number;
  psi_threshold?: number;
}

export function MlMonitoringPage() {
  const { user } = useAuth();
  const { data, isLoading } = useQuery({
    queryKey: ['ml-config'],
    queryFn: async () => {
      const { data: cfg } = await apiClient.get<HybridConfig>('/ml/config');
      return cfg;
    },
    enabled: user?.permissions.includes('HYBRID_WEIGHTS_CONFIG') ?? false,
  });

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Model monitoring</Typography.Title>
      <Typography.Paragraph type="secondary">
        Production scoring uses the registered Random Forest (v1). Full metrics and drift screens arrive in a later sprint.
      </Typography.Paragraph>
      <Card loading={isLoading}>
        {data && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="Hybrid weights">ML {data.alpha} + Rules {data.beta}</Descriptions.Item>
            <Descriptions.Item label="PASS below">{data.passThreshold ?? data.pass_threshold}</Descriptions.Item>
            <Descriptions.Item label="EXCEPTION from">{data.exceptionThreshold ?? data.exception_threshold}</Descriptions.Item>
            <Descriptions.Item label="PSI alert threshold">{data.psiThreshold ?? data.psi_threshold}</Descriptions.Item>
          </Descriptions>
        )}
      </Card>
    </div>
  );
}
