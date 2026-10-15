import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';

import { normalizeApiError } from '@/api/errors';
import { useAuth } from '@/auth/AuthContext';
import { postLoginPath } from '@/auth/postLoginPath';

interface LoginFormValues {
  username: string;
  password: string;
}

export function LoginPage() {
  const [form] = Form.useForm<LoginFormValues>();
  const { login, user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const expired = searchParams.get('expired') === '1';
  const from = (location.state as { from?: string } | null)?.from;

  useEffect(() => {
    if (user) {
      navigate(postLoginPath(user.role, from), { replace: true });
    }
  }, [user, navigate, from]);

  const onFinish = async (values: LoginFormValues) => {
    setSubmitting(true);
    setErrorMessage(null);
    try {
      const loggedIn = await login(values.username, values.password);
      navigate(postLoginPath(loggedIn.role, from), { replace: true });
    } catch (err) {
      const detail = normalizeApiError(err);
      setErrorMessage(detail.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
        background: 'linear-gradient(165deg, #1a4d7a 0%, #1a4d7a 38%, #e8ecf1 38%)',
      }}
    >
      <Card style={{ width: 400, maxWidth: '100%', boxShadow: '0 8px 24px rgba(0,0,0,0.12)' }}>
        <Typography.Title level={3} style={{ marginTop: 0, color: '#1a4d7a' }}>
          TradePerfect
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 24 }}>
          Sign in to book trades and review settlement risk
        </Typography.Paragraph>

        {expired && (
          <Alert
            type="warning"
            showIcon
            message="Your session expired. Please sign in again."
            style={{ marginBottom: 16 }}
          />
        )}
        {errorMessage && (
          <Alert type="error" showIcon message={errorMessage} style={{ marginBottom: 16 }} />
        )}

        <Typography.Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 16 }}>
          Demo logins (same password for all seeded users):
        </Typography.Paragraph>
        <Space wrap size={[8, 8]} style={{ marginBottom: 20 }}>
          {[
            { u: 'priya.sharma', label: 'Priya (trader)' },
            { u: 'sarah.chen', label: 'Sarah (trader)' },
            { u: 'john.miller', label: 'John (trader)' },
            { u: 'emma.davis', label: 'Emma (trader)' },
            { u: 'aisha.khan', label: 'Aisha (trader)' },
            { u: 'ops.user1', label: 'Ops user' },
            { u: 'ops.manager', label: 'Ops manager' },
            { u: 'ml.admin', label: 'ML admin' },
          ].map(({ u, label }) => (
            <Button key={u} size="small" onClick={() => form.setFieldsValue({ username: u })}>
              {label}
            </Button>
          ))}
        </Space>

        <Form<LoginFormValues> form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
          <Form.Item
            name="username"
            label="Username"
            rules={[{ required: true, message: 'Enter your username' }]}
          >
            <Input prefix={<UserOutlined />} placeholder="priya.sharma" autoComplete="username" />
          </Form.Item>
          <Form.Item
            name="password"
            label="Password"
            rules={[{ required: true, message: 'Enter your password' }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="Password"
              autoComplete="current-password"
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting} size="large">
            Sign in
          </Button>
        </Form>
      </Card>
    </div>
  );
}
