import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Radio,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Typography,
  message,
} from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';

import { normalizeApiError } from '@/api/errors';
import { getTradeTimeline } from '@/api/audit';
import { amendTrade, getTrade, getTradeHistory } from '@/api/trades';
import { useAuth } from '@/auth/AuthContext';
import { TradeResultCard } from '@/components/TradeResultCard';
import { StatusTag } from '@/components/StatusTag';
import { useReferenceData } from '@/hooks/useReferenceData';
import type { TradeInput } from '@/types/api';
import { fmtDate, fmtMoney, fmtNum, fmtScore } from '@/utils/format';

type FixFormValues = {
  tradeType: 'BUY' | 'SELL';
  symbol: string;
  quantity: number;
  price: number;
  counterparty: string;
  accountId: string;
  tradeDate: Dayjs;
  settlementDate: Dayjs;
  ssiMatched: boolean;
  currencyMatched: boolean;
  appliedFix: string;
};

function toAmendPayload(v: FixFormValues, queueId?: string): TradeInput & { queueId?: string; appliedFix?: string; changeReason?: string } {
  return {
    tradeType: v.tradeType,
    symbol: v.symbol,
    quantity: v.quantity,
    price: v.price,
    counterparty: v.counterparty,
    accountId: v.accountId,
    tradeDate: v.tradeDate.format('YYYY-MM-DD'),
    settlementDate: v.settlementDate.format('YYYY-MM-DD'),
    ssiMatched: v.ssiMatched,
    currencyMatched: v.currencyMatched,
    queueId,
    appliedFix: v.appliedFix,
    changeReason: v.appliedFix,
  };
}

export function TradeDetailPage() {
  const { tradeId } = useParams<{ tradeId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const qc = useQueryClient();
  const { symbols, counterparties, accounts } = useReferenceData();
  const queueId = (location.state as { queueId?: string } | null)?.queueId;
  const canApplyFix = Boolean(queueId && user?.permissions.includes('FIX_APPLY'));

  const { data: trade, isLoading } = useQuery({
    queryKey: ['trade', tradeId],
    queryFn: () => getTrade(tradeId!),
    enabled: !!tradeId,
  });

  const { data: history } = useQuery({
    queryKey: ['trade-history', tradeId],
    queryFn: () => getTradeHistory(tradeId!),
    enabled: !!tradeId,
  });

  const { data: timeline } = useQuery({
    queryKey: ['trade-audit', tradeId],
    queryFn: () => getTradeTimeline(tradeId!),
    enabled: !!tradeId,
  });

  const [fixForm] = Form.useForm<FixFormValues>();

  useEffect(() => {
    if (!trade || !canApplyFix) return;
    fixForm.setFieldsValue({
      tradeType: trade.tradeType as 'BUY' | 'SELL',
      symbol: trade.symbol ?? undefined,
      quantity: trade.quantity ?? undefined,
      price: trade.price ?? undefined,
      counterparty: trade.counterparty ?? undefined,
      accountId: trade.accountId ?? undefined,
      tradeDate: trade.tradeDate ? dayjs(trade.tradeDate) : undefined,
      settlementDate: trade.settlementDate ? dayjs(trade.settlementDate) : undefined,
      ssiMatched: trade.ssiMatched ?? false,
      currencyMatched: trade.currencyMatched ?? true,
      appliedFix: '',
    });
  }, [trade, canApplyFix, fixForm]);

  const amendMut = useMutation({
    mutationFn: (values: FixFormValues) => {
      if (!trade || trade.status === 'REJECTED') throw new Error('Cannot amend');
      return amendTrade(tradeId!, toAmendPayload(values, queueId));
    },
    onSuccess: () => {
      message.success('Fix applied');
      void qc.invalidateQueries({ queryKey: ['trade', tradeId] });
      navigate('/repair-queue');
    },
    onError: (e) => message.error(normalizeApiError(e).message),
  });

  if (!tradeId) return null;
  if (isLoading || !trade) return <Card loading />;

  const readOnly = trade.status === 'REJECTED';
  const detailsEditable = canApplyFix && !readOnly;

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button onClick={() => navigate(-1)}>Back</Button>
        <Typography.Title level={4} style={{ margin: 0 }}>Trade {trade.tradeId}</Typography.Title>
        <StatusTag status={trade.status} />
      </Space>

      {queueId && (
        <Alert type="info" showIcon message={`Applying fix for queue item ${queueId}`} style={{ marginBottom: 16 }} />
      )}

      {trade.status === 'REJECTED' && (
        <Alert
          type="error"
          showIcon
          message="This trade was rejected at booking"
          description="Correct it in the upstream system and upload again. It cannot be amended here."
          style={{ marginBottom: 16 }}
        />
      )}

      <Tabs
        items={[
          {
            key: 'details',
            label: 'Details',
            children: (
              <>
                {detailsEditable ? (
                  <Card title="Trade details (editable)" style={{ marginBottom: 16 }}>
                    <Form form={fixForm} layout="vertical" onFinish={(v) => amendMut.mutate(v)}>
                      <Row gutter={16}>
                        <Col xs={24} sm={12}>
                          <Form.Item name="symbol" label="Symbol" rules={[{ required: true }]}>
                            <Select
                              showSearch
                              options={symbols.data?.map((s) => ({ value: s.symbol, label: s.symbol })) ?? []}
                              loading={symbols.isLoading}
                            />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="tradeType" label="Type" rules={[{ required: true }]}>
                            <Radio.Group>
                              <Radio value="BUY">BUY</Radio>
                              <Radio value="SELL">SELL</Radio>
                            </Radio.Group>
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="quantity" label="Quantity" rules={[{ required: true }]}>
                            <InputNumber min={1} style={{ width: '100%' }} />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="price" label="Price" rules={[{ required: true }]}>
                            <InputNumber min={0} step={0.01} style={{ width: '100%' }} />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="counterparty" label="Counterparty" rules={[{ required: true }]}>
                            <Select
                              showSearch
                              options={counterparties.data?.map((c) => ({ value: c.name, label: c.name })) ?? []}
                              loading={counterparties.isLoading}
                            />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item label="Trader">
                            <Input value={trade.traderName ?? ''} disabled />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="tradeDate" label="Trade date" rules={[{ required: true }]}>
                            <DatePicker style={{ width: '100%' }} />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="settlementDate" label="Settlement" rules={[{ required: true }]}>
                            <DatePicker style={{ width: '100%' }} />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="accountId" label="Account" rules={[{ required: true }]}>
                            <Select
                              options={accounts.data?.map((a) => ({ value: a.accountId, label: a.accountId })) ?? []}
                              loading={accounts.isLoading}
                            />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="ssiMatched" label="SSI matched" valuePropName="checked">
                            <Switch />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item name="currencyMatched" label="Currency matched" valuePropName="checked">
                            <Switch />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item label="Risk score">
                            <Input value={fmtScore(trade.finalScore)} disabled />
                          </Form.Item>
                        </Col>
                        <Col xs={24} sm={12}>
                          <Form.Item label="Version">
                            <Input value={String(trade.version)} disabled />
                          </Form.Item>
                        </Col>
                      </Row>
                      <Form.Item name="appliedFix" label="What you changed" rules={[{ required: true, min: 3 }]}>
                        <Input.TextArea rows={3} />
                      </Form.Item>
                      <Button type="primary" htmlType="submit" className="tp-btn-bnp" loading={amendMut.isPending}>
                        Save fix
                      </Button>
                    </Form>
                  </Card>
                ) : (
                  <Card size="small" style={{ marginBottom: 16 }}>
                    <Row gutter={[16, 8]}>
                      <Col span={12}><Typography.Text type="secondary">Symbol</Typography.Text><div>{trade.symbol}</div></Col>
                      <Col span={12}><Typography.Text type="secondary">Type</Typography.Text><div>{trade.tradeType}</div></Col>
                      <Col span={12}><Typography.Text type="secondary">Quantity</Typography.Text><div>{fmtNum(trade.quantity)}</div></Col>
                      <Col span={12}><Typography.Text type="secondary">Price</Typography.Text><div>{fmtMoney(trade.price)}</div></Col>
                      <Col span={12}><Typography.Text type="secondary">Counterparty</Typography.Text><div>{trade.counterparty}</div></Col>
                      <Col span={12}><Typography.Text type="secondary">Trader</Typography.Text><div>{trade.traderName}</div></Col>
                      <Col span={12}><Typography.Text type="secondary">Trade date</Typography.Text><div>{fmtDate(trade.tradeDate)}</div></Col>
                      <Col span={12}><Typography.Text type="secondary">Settlement</Typography.Text><div>{fmtDate(trade.settlementDate)}</div></Col>
                      <Col span={12}><Typography.Text type="secondary">SSI matched</Typography.Text><div>{trade.ssiMatched ? 'Yes' : 'No'}</div></Col>
                      <Col span={12}><Typography.Text type="secondary">Currency matched</Typography.Text><div>{trade.currencyMatched ? 'Yes' : 'No'}</div></Col>
                      <Col span={12}><Typography.Text type="secondary">Risk score</Typography.Text><div>{fmtScore(trade.finalScore)}</div></Col>
                      <Col span={12}><Typography.Text type="secondary">Version</Typography.Text><div>{trade.version}</div></Col>
                    </Row>
                  </Card>
                )}
                <TradeResultCard result={trade} />
              </>
            ),
          },
          {
            key: 'history',
            label: `Versions (${trade.versionsCount})`,
            children: (
              <Table
                rowKey="version"
                dataSource={history ?? []}
                pagination={false}
                columns={[
                  { title: 'Ver', dataIndex: 'version', width: 60 },
                  { title: 'Type', dataIndex: 'changeType' },
                  { title: 'By', dataIndex: 'changedBy' },
                  { title: 'When', dataIndex: 'changedAt', render: (v: string) => new Date(v).toLocaleString() },
                  { title: 'Reason', dataIndex: 'changeReason', ellipsis: true },
                ]}
              />
            ),
          },
          {
            key: 'audit',
            label: 'Audit',
            children: (
              <Table
                rowKey="id"
                dataSource={timeline ?? []}
                pagination={false}
                columns={[
                  { title: 'Time', dataIndex: 'eventTime', render: (v: string) => new Date(v).toLocaleString() },
                  { title: 'User', dataIndex: 'actorUsername' },
                  { title: 'Action', dataIndex: 'action' },
                  { title: 'Status', dataIndex: 'statusAfter' },
                ]}
              />
            ),
          },
        ]}
      />
    </div>
  );
}
