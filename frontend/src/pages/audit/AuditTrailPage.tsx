import { Button, Card, Descriptions, Input, Space, Table, Timeline, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { getTradeAuditBox, getTradeTimeline, searchAudit, verifyAuditChain } from '@/api/audit';
import { useAuth } from '@/auth/AuthContext';
import { fmtDate, fmtScore } from '@/utils/format';

export function AuditTrailPage() {
  const { user } = useAuth();
  const [page, setPage] = useState(1);
  const [tradeId, setTradeId] = useState('');
  const [searchTrade, setSearchTrade] = useState('');

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['audit', page, tradeId],
    queryFn: () => searchAudit({ page, pageSize: 30, tradeId: tradeId || undefined }),
  });

  const timeline = useQuery({
    queryKey: ['audit-timeline', searchTrade],
    queryFn: () => getTradeTimeline(searchTrade),
    enabled: searchTrade.length >= 3,
  });

  const auditBox = useQuery({
    queryKey: ['audit-box', searchTrade],
    queryFn: () => getTradeAuditBox(searchTrade),
    enabled: searchTrade.length >= 3,
  });

  const columns = [
    { title: 'Time', dataIndex: 'eventTime', key: 'eventTime', render: (v: string) => new Date(v).toLocaleString() },
    { title: 'User', dataIndex: 'actorUsername', key: 'actorUsername' },
    { title: 'Action', dataIndex: 'action', key: 'action' },
    { title: 'Trade', dataIndex: 'tradeId', key: 'tradeId' },
    { title: 'Queue', dataIndex: 'queueId', key: 'queueId' },
    { title: 'Status', dataIndex: 'statusAfter', key: 'statusAfter' },
  ];

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Audit trail</Typography.Title>
      <Typography.Paragraph type="secondary">
        Immutable log of bookings, queue actions, fixes and exports. Search by trade ID to see the full lifecycle.
      </Typography.Paragraph>

      <Card style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input
            placeholder="Filter list by trade ID"
            value={tradeId}
            onChange={(e) => setTradeId(e.target.value)}
            style={{ width: 200 }}
          />
          <Button onClick={() => refetch()}>Search</Button>
          {user?.permissions.includes('USER_MANAGE') && (
            <Button
              onClick={async () => {
                const r = await verifyAuditChain();
                alert(r.valid ? 'Audit chain is valid' : `Chain broken at row #${r.brokenAtId}`);
              }}
            >
              Verify integrity
            </Button>
          )}
        </Space>
      </Card>

      <Table
        rowKey="id"
        loading={isLoading}
        dataSource={data?.items ?? []}
        columns={columns}
        pagination={{
          current: page,
          pageSize: 30,
          total: data?.total ?? 0,
          onChange: setPage,
        }}
        style={{ marginBottom: 24 }}
      />

      <Card title="Trade lifecycle">
        <Input.Search
          placeholder="Enter trade ID (e.g. T110003)"
          enterButton="Load history"
          onSearch={setSearchTrade}
          style={{ maxWidth: 360, marginBottom: 16 }}
        />
        {auditBox.data && (
          <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }}>
            <Descriptions.Item label="Trade">{auditBox.data.tradeId}</Descriptions.Item>
            <Descriptions.Item label="Queue">{auditBox.data.queueId ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="Risk">{fmtScore(auditBox.data.riskScore)}</Descriptions.Item>
            <Descriptions.Item label="Status">{auditBox.data.status}</Descriptions.Item>
            <Descriptions.Item label="Likely reason" span={2}>{auditBox.data.likelyReason ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="Applied fix" span={2}>{auditBox.data.appliedFix ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="Applied by">{auditBox.data.appliedBy ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="Applied at">{auditBox.data.appliedTimestamp ? fmtDate(auditBox.data.appliedTimestamp) : '—'}</Descriptions.Item>
          </Descriptions>
        )}
        {timeline.data && (
          <Timeline
            items={timeline.data.map((e) => ({
              children: (
                <div>
                  <Typography.Text strong>{e.action}</Typography.Text>
                  <Typography.Text type="secondary"> · {new Date(e.eventTime).toLocaleString()} · {e.actorUsername}</Typography.Text>
                  {e.statusAfter && <div>Status → {e.statusAfter}</div>}
                  {e.appliedFix && <div>Fix: {e.appliedFix}</div>}
                </div>
              ),
            }))}
          />
        )}
      </Card>
    </div>
  );
}
