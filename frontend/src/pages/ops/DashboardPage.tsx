import { Card, Col, Row, Space, Statistic, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';

import { getDashboardSummary } from '@/api/dashboard';

export function DashboardPage() {
  const { data, isLoading } = useQuery({ queryKey: ['dashboard-summary'], queryFn: () => getDashboardSummary() });

  const block = (title: string, s: { total: number; pass: number; monitor: number; exception: number; rejected: number } | undefined) => (
    <Card title={title} loading={isLoading}>
      <Row gutter={16}>
        <Col span={8}><Statistic title="Total" value={s?.total ?? 0} /></Col>
        <Col span={4}><Statistic title="PASS" value={s?.pass ?? 0} valueStyle={{ color: '#389e0d' }} /></Col>
        <Col span={4}><Statistic title="MONITOR" value={s?.monitor ?? 0} /></Col>
        <Col span={4}><Statistic title="EXCEPTION" value={s?.exception ?? 0} valueStyle={{ color: '#cf1322' }} /></Col>
        <Col span={4}><Statistic title="REJECTED" value={s?.rejected ?? 0} /></Col>
      </Row>
    </Card>
  );

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Exception dashboard</Typography.Title>
      <Typography.Paragraph type="secondary">
        Overview of booked trades by risk classification. Use the repair queue to work open exceptions.
      </Typography.Paragraph>
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        {block('Today', data?.today)}
        {block('All time', data?.historical)}
      </Space>
    </div>
  );
}
