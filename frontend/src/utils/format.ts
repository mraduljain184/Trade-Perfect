import dayjs from 'dayjs';

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return dayjs(iso).format('YYYY-MM-DD');
}

export function fmtNum(n: number | null | undefined): string {
  if (n == null) return '—';
  return n.toLocaleString('en-US');
}

export function fmtScore(n: number | null | undefined): string {
  if (n == null) return '—';
  return n.toFixed(4);
}

export function fmtMoney(n: number | null | undefined): string {
  if (n == null) return '—';
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
}
