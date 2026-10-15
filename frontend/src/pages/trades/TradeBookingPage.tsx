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
  Typography,
} from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useCallback, useMemo, useRef, useState } from 'react';

import { normalizeApiError } from '@/api/errors';
import { predictTrade, type PredictResult } from '@/api/ml';
import { createTrade, validateTrade } from '@/api/trades';
import { SettlementChecksHelp } from '@/components/SettlementChecksHelp';
import { StatusTag } from '@/components/StatusTag';
import { TradeResultCard } from '@/components/TradeResultCard';
import { useAuth } from '@/auth/AuthContext';
import { useReferenceData } from '@/hooks/useReferenceData';
import type { FieldError, TradeInput, TradeResult } from '@/types/api';

type FormValues = {
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
};

function toPayload(v: FormValues): TradeInput {
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
  };
}

const SCENARIOS: Record<string, Partial<FormValues>> = {
  pass: {
    tradeType: 'BUY',
    symbol: 'AAPL',
    quantity: 500,
    price: 180.5,
    counterparty: 'BlackRock',
    accountId: 'ACC1001',
    ssiMatched: true,
    currencyMatched: true,
  },
  monitor: {
    tradeType: 'BUY',
    symbol: 'NVDA',
    quantity: 1200,
    price: 450,
    counterparty: 'Nomura',
    accountId: 'ACC1006',
    ssiMatched: true,
    currencyMatched: true,
  },
  exception: {
    tradeType: 'BUY',
    symbol: 'MSFT',
    quantity: 800,
    price: 380,
    counterparty: 'Barclays',
    accountId: 'ACC1001',
    ssiMatched: false,
    currencyMatched: true,
  },
};

export function TradeBookingPage() {
  const { user } = useAuth();
  const [form] = Form.useForm<FormValues>();
  const { symbols, counterparties, accounts } = useReferenceData();
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [warnings, setWarnings] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<TradeResult | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [mlPreview, setMlPreview] = useState<PredictResult | null>(null);
  const [mlLoading, setMlLoading] = useState(false);

  const initial = useMemo(
    () => ({
      tradeType: 'BUY' as const,
      tradeDate: dayjs(),
      settlementDate: dayjs().add(2, 'day'),
      ssiMatched: true,
      currencyMatched: true,
      quantity: 500,
      price: 100,
    }),
    [],
  );

  const validateTimer = useRef<number | null>(null);

  const runValidate = useCallback(async () => {
    try {
      const v = await form.validateFields();
      const payload = toPayload(v);
      const res = await validateTrade(payload);
      const map: Record<string, string> = {};
      res.fields.forEach((e: FieldError) => {
        map[e.field] = e.message;
      });
      setServerErrors(map);
      setWarnings(res.warnings);

      setMlLoading(true);
      try {
        const pred = await predictTrade(payload);
        setMlPreview(pred);
        pred.rejectionReasons?.forEach((e) => {
          if (e.field) map[e.field] = e.message;
        });
        setServerErrors(map);
      } catch {
        setMlPreview(null);
      } finally {
        setMlLoading(false);
      }
    } catch {
      setMlPreview(null);
    }
  }, [form]);

  const applyScenario = (key: string) => {
    const s = SCENARIOS[key];
    if (!s) return;
    form.setFieldsValue({ ...initial, ...s, tradeDate: dayjs(), settlementDate: dayjs().add(4, 'day') });
    void runValidate();
  };

  const onFinish = async (v: FormValues) => {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await createTrade(toPayload(v));
      setResult(created);
    } catch (err) {
      const detail = normalizeApiError(err);
      if (detail.fields?.length) {
        const map: Record<string, string> = {};
        detail.fields.forEach((e) => {
          map[e.field] = e.message;
        });
        setServerErrors(map);
      }
      setSubmitError(detail.message);
    } finally {
      setSubmitting(false);
    }
  };

  const help = (name: string) => serverErrors[name];

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Book a trade</Typography.Title>
      <Typography.Paragraph type="secondary">
        Fill in the details below. As you type, we run the same checks used on submit so you can fix issues early.
        Nothing is saved until you click Submit.
      </Typography.Paragraph>

      <Card size="small" title="Load an example" style={{ marginBottom: 16 }}>
        <Typography.Paragraph type="secondary" style={{ marginTop: 0, marginBottom: 12, fontSize: 13 }}>
          Demo rows from our sample data — useful for a quick walkthrough.
        </Typography.Paragraph>
        <Space wrap>
          <Button size="small" onClick={() => applyScenario('pass')}>Low risk</Button>
          <Button size="small" onClick={() => applyScenario('monitor')}>Medium risk</Button>
          <Button size="small" onClick={() => applyScenario('exception')}>SSI not matched</Button>
        </Space>
      </Card>

      {warnings.length > 0 && (
        <Alert type="warning" showIcon message={warnings.join(' · ')} style={{ marginBottom: 16 }} />
      )}
      {submitError && <Alert type="error" showIcon message={submitError} style={{ marginBottom: 16 }} />}

      {mlPreview && (
        <Alert
          type={mlPreview.status === 'REJECTED' ? 'error' : 'info'}
          showIcon
          style={{ marginBottom: 16 }}
          message={
            <Space wrap>
              <span>{mlLoading ? 'Checking…' : 'Pre-submit check'}</span>
              <StatusTag status={mlPreview.status} />
              {mlPreview.mlScore != null && (
                <Typography.Text type="secondary">
                  Risk score {mlPreview.finalScore.toFixed(2)}
                  {' '}(rules {mlPreview.ruleScore?.toFixed(2) ?? '—'}, model {mlPreview.mlScore.toFixed(2)})
                </Typography.Text>
              )}
            </Space>
          }
          description={mlPreview.likelyReason ?? mlPreview.explanation ?? 'No issues flagged yet.'}
        />
      )}

      <Card>
        <Form
          form={form}
          layout="vertical"
          initialValues={initial}
          onFinish={onFinish}
          onValuesChange={() => {
            if (validateTimer.current) window.clearTimeout(validateTimer.current);
            validateTimer.current = window.setTimeout(() => void runValidate(), 500);
          }}
        >
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item label="Trade ID">
                <Input disabled value="Auto-generated on submit" />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item label="Trader name">
                <Input disabled value={user?.fullName ?? ''} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="tradeType" label="Trade type" rules={[{ required: true }]}>
                <Radio.Group optionType="button" buttonStyle="solid" options={[{ label: 'BUY', value: 'BUY' }, { label: 'SELL', value: 'SELL' }]} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                name="symbol"
                label="Symbol"
                rules={[{ required: true }]}
                validateStatus={help('symbol') ? 'error' : undefined}
                help={help('symbol')}
              >
                <Select
                  showSearch
                  placeholder="Select symbol"
                  loading={symbols.isLoading}
                  options={symbols.data?.map((s) => ({ value: s.symbol, label: s.symbol }))}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item
                name="quantity"
                label="Quantity"
                rules={[{ required: true, type: 'number', min: 1 }]}
                validateStatus={help('quantity') ? 'error' : undefined}
                help={help('quantity') || 'Positive integer'}
              >
                <InputNumber style={{ width: '100%' }} min={1} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item
                name="price"
                label="Price (USD)"
                rules={[{ required: true, type: 'number', min: 0.0001 }]}
                validateStatus={help('price') ? 'error' : undefined}
                help={help('price')}
              >
                <InputNumber style={{ width: '100%' }} min={0} step={0.0001} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item
                name="counterparty"
                label="Counterparty"
                rules={[{ required: true }]}
                validateStatus={help('counterparty') ? 'error' : undefined}
                help={help('counterparty')}
              >
                <Select
                  showSearch
                  placeholder="Select counterparty"
                  loading={counterparties.isLoading}
                  options={counterparties.data?.map((c) => ({
                    value: c.name,
                    label: `${c.name} (${c.tierLabel})`,
                  }))}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                name="accountId"
                label="Account ID"
                rules={[{ required: true }]}
                validateStatus={help('accountId') ? 'error' : undefined}
                help={help('accountId')}
              >
                <Select
                  placeholder="Select account"
                  loading={accounts.isLoading}
                  options={accounts.data?.map((a) => ({
                    value: a.accountId,
                    label: a.isActive ? a.accountId : `${a.accountId} (Inactive)`,
                    disabled: !a.isActive,
                  }))}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item name="tradeDate" label="Trade date" rules={[{ required: true }]}>
                <DatePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item
                name="settlementDate"
                label="Settlement date"
                rules={[{ required: true }]}
                validateStatus={help('settlementDate') ? 'error' : undefined}
                help={help('settlementDate') || '>= trade date'}
              >
                <DatePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24}>
              <SettlementChecksHelp />
            </Col>
            <Col xs={24} md={6}>
              <Form.Item
                name="ssiMatched"
                label="SSI matched?"
                valuePropName="checked"
                tooltip="Standard Settlement Instructions — pay/receive account details agree with the counterparty"
              >
                <Switch checkedChildren="Yes" unCheckedChildren="No" />
              </Form.Item>
            </Col>
            <Col xs={24} md={6}>
              <Form.Item
                name="currencyMatched"
                label="Currency matched?"
                valuePropName="checked"
                tooltip="Trade currency matches the account and counterparty setup"
              >
                <Switch checkedChildren="Yes" unCheckedChildren="No" />
              </Form.Item>
            </Col>
          </Row>
          <Space>
            <Button onClick={() => { form.resetFields(); setResult(null); setServerErrors({}); }}>Reset</Button>
            <Button type="primary" htmlType="submit" loading={submitting}>
              Submit trade
            </Button>
          </Space>
        </Form>
      </Card>

      {result && (
        <TradeResultCard
          result={result}
          onBookAnother={() => {
            setResult(null);
            form.resetFields();
          }}
        />
      )}
    </div>
  );
}
