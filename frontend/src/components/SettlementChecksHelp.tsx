import { Typography } from 'antd';

/** Short copy traders see on the booking form — not API docs. */
export function SettlementChecksHelp() {
  return (
    <Typography.Paragraph type="secondary" style={{ marginBottom: 0, fontSize: 13 }}>
      <strong>SSI matched</strong> — settlement instructions (bank/custodian details) already line up with the
      counterparty. <strong>Currency matched</strong> — the trade currency matches what the account and
      counterparty expect. If either is <em>No</em>, settlement is more likely to fail and the risk score goes up.
    </Typography.Paragraph>
  );
}
