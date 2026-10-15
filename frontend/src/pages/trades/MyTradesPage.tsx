import { EyeOutlined, PlusOutlined, ToolOutlined } from '@ant-design/icons';
import { Button, Card, Col, Input, Row, Select, Space, Statistic, Table, Typography } from 'antd';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { listTrades } from '@/api/trades';
import { useAuth } from '@/auth/AuthContext';
import { isTrader } from '@/auth/roles';
import { showRepairQueueNav } from '@/config/nav';
import { StatusTag } from '@/components/StatusTag';
import type { TradeResult, TradeStatus } from '@/types/api';
import { fmtDate, fmtMoney, fmtNum, fmtScore } from '@/utils/format';

export function MyTradesPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const showQueue = showRepairQueueNav(user);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<string | undefined>();
  const [search, setSearch] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['my-trades', page, status, search],
    queryFn: () =>
      listTrades({
        page,
        pageSize: 20,
        mine: true,
        status: status || undefined,
        search: search || undefined,
        sort: 'createdAt:desc',
      }),
  });

  const stats = useMemo(() => {
    const items = data?.items ?? [];
    const count = (s: TradeStatus) => items.filter((t) => t.status === s).length;
    return {
      total: data?.total ?? 0,
      pass: count('PASS'),
      monitor: count('MONITOR'),
      exception: count('EXCEPTION'),
    };
  }, [data]);

  const columns = [
    { title: 'Trade ID', dataIndex: 'tradeId', key: 'tradeId', render: (v: string) => v ?? '—' },
    { title: 'Symbol', dataIndex: 'symbol', key: 'symbol' },
    {
      title: 'Type',
      dataIndex: 'tradeType',
      key: 'tradeType',
      render: (v: string) => (
        <Typography.Text type={v === 'BUY' ? 'success' : 'danger'}>{v}</Typography.Text>
      ),
    },
    { title: 'Qty', dataIndex: 'quantity', key: 'quantity', render: fmtNum },
    { title: 'Price', dataIndex: 'price', key: 'price', render: fmtMoney },
    { title: 'Counterparty', dataIndex: 'counterparty', key: 'counterparty', ellipsis: true },
    { title: 'Settlement', dataIndex: 'settlementDate', key: 'settlementDate', render: fmtDate },
    { title: 'Risk score', dataIndex: 'finalScore', key: 'finalScore', render: fmtScore },
    { title: 'Status', dataIndex: 'status', key: 'status', render: (s: TradeStatus) => <StatusTag status={s} /> },
    { title: 'Priority', dataIndex: 'priority', key: 'priority', render: (p: string | null) => p ?? '—' },
    {
      title: 'Action',
      key: 'action',
      render: (_: unknown, row: TradeResult) => (
        <Button size="small" icon={<EyeOutlined />} onClick={() => navigate(`/trades/${row.tradeId}`)}>
          Details
        </Button>
      ),
    },
  ];

  return (
    <div>
      <Row justify="space-between" align="middle" style={{ marginBottom: 16 }}>
        <div>
          <Typography.Title level={4} style={{ margin: 0 }}>My trades</Typography.Title>
          <Typography.Text type="secondary">Trades you booked or uploaded</Typography.Text>
        </div>
        <Space>
          {showQueue && (
            <Button icon={<ToolOutlined />} onClick={() => navigate('/repair-queue')}>
              Repair queue
            </Button>
          )}
          {isTrader(user) && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/trades/new')}>
              Book new trade
            </Button>
          )}
        </Space>
      </Row>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}><Card size="small"><Statistic title="Total (filtered)" value={stats.total} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="PASS" value={stats.pass} valueStyle={{ color: '#3f8600' }} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="MONITOR" value={stats.monitor} valueStyle={{ color: '#d48806' }} /></Card></Col>
        <Col span={6}><Card size="small"><Statistic title="EXCEPTION" value={stats.exception} valueStyle={{ color: '#cf1322' }} /></Card></Col>
      </Row>

      <Card>
        <Space wrap style={{ marginBottom: 16 }}>
          <Input.Search
            placeholder="Search trade ID, symbol, counterparty"
            allowClear
            onSearch={(v) => { setSearch(v); setPage(1); }}
            style={{ width: 280 }}
          />
          <Select
            allowClear
            placeholder="Status"
            style={{ width: 160 }}
            onChange={(v) => { setStatus(v); setPage(1); }}
            options={['PASS', 'MONITOR', 'EXCEPTION', 'REJECTED'].map((s) => ({ value: s, label: s }))}
          />
        </Space>
        <Table
          rowKey="id"
          loading={isLoading}
          columns={columns}
          dataSource={data?.items ?? []}
          size="small"
          pagination={{
            current: page,
            pageSize: 20,
            total: data?.total ?? 0,
            onChange: setPage,
          }}
        />
      </Card>
    </div>
  );
}
