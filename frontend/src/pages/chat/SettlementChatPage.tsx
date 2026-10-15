import { RobotOutlined, SendOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Input, Space, Spin, Typography, message } from 'antd';
import { useMutation } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { sendChatMessage } from '@/api/chat';
import { normalizeApiError } from '@/api/errors';

const SESSION_KEY = 'tp-chat-session-id';

function getSessionId(): string {
  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

type ChatLine = { role: 'user' | 'assistant'; text: string; tool?: string | null };

const STARTERS = [
  'Why is HT00697 an exception?',
  'Which rules triggered for HT00697?',
  'Failures for counterparty Raymond James',
  'Trades settling in the next 14 days',
  'Top 5 riskiest counterparties',
];

export function SettlementChatPage() {
  const [lines, setLines] = useState<ChatLine[]>([]);
  const [draft, setDraft] = useState('');
  const sessionId = useMemo(() => getSessionId(), []);

  const sendMut = useMutation({
    mutationFn: (text: string) => sendChatMessage(sessionId, text),
    onMutate: (text) => {
      setLines((prev) => [...prev, { role: 'user', text }]);
      setDraft('');
    },
    onSuccess: (res) => {
      setLines((prev) => [...prev, { role: 'assistant', text: res.answer, tool: res.toolUsed }]);
    },
    onError: (e) => message.error(normalizeApiError(e).message),
  });

  const submit = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || sendMut.isPending) return;
    sendMut.mutate(trimmed);
  };

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>
        <RobotOutlined /> Settlement assistant
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        Ask about settlement failures, triggered rules, risky counterparties, and trade explanations. Answers use live
        trade data and historical failure counts.
      </Typography.Paragraph>

      <Card style={{ marginBottom: 16 }}>
        <Typography.Text type="secondary">Try:</Typography.Text>
        <Space wrap style={{ marginTop: 8 }}>
          {STARTERS.map((q) => (
            <Button key={q} size="small" onClick={() => submit(q)} disabled={sendMut.isPending}>
              {q}
            </Button>
          ))}
        </Space>
      </Card>

      <Card
        styles={{ body: { minHeight: 320, maxHeight: 480, overflowY: 'auto', background: '#fafafa' } }}
      >
        {lines.length === 0 && (
          <Alert
            type="info"
            showIcon
            message="No messages yet"
            description="Ask a question about a trade id, counterparty, or settlement window."
          />
        )}
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          {lines.map((line, i) => (
            <div
              key={i}
              style={{
                alignSelf: line.role === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '92%',
                padding: '10px 14px',
                borderRadius: 8,
                background: line.role === 'user' ? '#00965e' : '#fff',
                color: line.role === 'user' ? '#fff' : 'inherit',
                border: line.role === 'assistant' ? '1px solid #e8e8e8' : undefined,
                whiteSpace: 'pre-wrap',
              }}
            >
              {line.text}
              {line.tool && line.role === 'assistant' && (
                <Typography.Text type="secondary" style={{ display: 'block', fontSize: 11, marginTop: 6 }}>
                  via {line.tool}
                </Typography.Text>
              )}
            </div>
          ))}
          {sendMut.isPending && <Spin size="small" />}
        </Space>
      </Card>

      <Space.Compact style={{ width: '100%', marginTop: 16 }}>
        <Input.TextArea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="e.g. Explain trade HT03433 and what settlement check failed"
          autoSize={{ minRows: 2, maxRows: 4 }}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              submit(draft);
            }
          }}
        />
        <Button
          type="primary"
          className="tp-btn-bnp"
          icon={<SendOutlined />}
          loading={sendMut.isPending}
          onClick={() => submit(draft)}
        >
          Send
        </Button>
      </Space.Compact>
    </div>
  );
}
