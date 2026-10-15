import { Alert, Card, Descriptions, Progress, Space, Typography } from 'antd';

import { StatusTag } from '@/components/StatusTag';
import type { TradeResult } from '@/types/api';
import { fmtScore } from '@/utils/format';

export function TradeResultCard({ result, onBookAnother }: { result: TradeResult; onBookAnother?: () => void }) {
  const rejected = result.status === 'REJECTED';

  return (
    <Card
      title="Result"
      style={{ marginTop: 24 }}
      extra={onBookAnother ? (
        <a onClick={onBookAnother}>Book another trade</a>
      ) : null}
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Space wrap>
          <StatusTag status={result.status} />
          {result.priority && <Typography.Text type="secondary">Priority: {result.priority}</Typography.Text>}
          {result.queueId && <Typography.Text>Queue: {result.queueId}</Typography.Text>}
        </Space>

        {rejected && result.rejectionReasons?.length ? (
          <Alert
            type="error"
            message="Trade rejected"
            description={
              <ul style={{ margin: 0, paddingLeft: 20 }}>
                {result.rejectionReasons.map((r) => (
                  <li key={r.ruleId}>{r.message}</li>
                ))}
              </ul>
            }
          />
        ) : (
          <>
            <Descriptions size="small" column={3}>
              <Descriptions.Item label="Overall risk">{fmtScore(result.finalScore)}</Descriptions.Item>
              <Descriptions.Item label="From rules">{fmtScore(result.ruleScore)}</Descriptions.Item>
              <Descriptions.Item label="From model">{fmtScore(result.mlScore)}</Descriptions.Item>
            </Descriptions>
            <Progress percent={Math.round(result.finalScore * 100)} status={result.finalScore >= 0.7 ? 'exception' : 'normal'} />
            {result.likelyReason && (
              <Typography.Paragraph>
                <strong>What we noticed:</strong> {result.likelyReason}
              </Typography.Paragraph>
            )}
            {result.suggestedAction && (
              <Typography.Paragraph>
                <strong>What to do:</strong> {result.suggestedAction}
              </Typography.Paragraph>
            )}
            {result.explanation && (
              <Typography.Paragraph type="secondary">{result.explanation}</Typography.Paragraph>
            )}
          </>
        )}
      </Space>
    </Card>
  );
}
