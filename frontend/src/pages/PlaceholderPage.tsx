import { Typography } from 'antd';

export function PlaceholderPage({ title }: { title: string }) {
  return (
    <div>
      <Typography.Title level={4}>{title}</Typography.Title>
      <Typography.Paragraph type="secondary">
        This screen is not built yet. You are signed in and the API session is active.
      </Typography.Paragraph>
    </div>
  );
}
