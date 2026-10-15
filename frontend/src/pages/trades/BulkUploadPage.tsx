import { InboxOutlined } from '@ant-design/icons';
import { Alert, Card, Progress, Statistic, Typography, Upload } from 'antd';
import type { UploadProps } from 'antd';
import { useEffect, useRef, useState } from 'react';

import { normalizeApiError } from '@/api/errors';
import { getUploadBatch, uploadBulkCsv } from '@/api/uploads';
import type { UploadBatch } from '@/types/api';

const { Dragger } = Upload;

export function BulkUploadPage() {
  const [batch, setBatch] = useState<UploadBatch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) window.clearInterval(pollRef.current);
    };
  }, []);

  const pollBatch = (batchId: string) => {
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      try {
        const b = await getUploadBatch(batchId);
        setBatch(b);
        if (b.status === 'COMPLETED' || b.status === 'FAILED') {
          if (pollRef.current) window.clearInterval(pollRef.current);
        }
      } catch {
        /* ignore transient errors */
      }
    }, 2000);
  };

  const props: UploadProps = {
    accept: '.csv',
    maxCount: 1,
    showUploadList: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      setUploading(true);
      setError(null);
      setBatch(null);
      try {
        const f = file as File;
        const { batchId } = await uploadBulkCsv(f);
        const initial = await getUploadBatch(batchId);
        setBatch(initial);
        pollBatch(batchId);
        onSuccess?.(initial);
      } catch (err) {
        const detail = normalizeApiError(err);
        setError(detail.message);
        onError?.(new Error(detail.message));
      } finally {
        setUploading(false);
      }
    },
  };

  const pct = batch && batch.totalRows > 0
    ? Math.round((batch.processedRows / batch.totalRows) * 100)
    : 0;

  return (
    <div>
      <Typography.Title level={4} style={{ marginTop: 0 }}>Upload trades from CSV</Typography.Title>
      <Typography.Paragraph type="secondary">
        Drop a file from your booking system. We validate each row, score settlement risk, and show a summary when
        processing finishes. Large files are handled in batches of 10,000 rows.
      </Typography.Paragraph>

      {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}

      <Card>
        <Dragger {...props} disabled={uploading}>
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">Click or drag a CSV trade file here</p>
          <p className="ant-upload-hint">.csv up to 20 MB · UTF-8 with optional BOM</p>
        </Dragger>
      </Card>

      {batch && (
        <Card title={`Batch ${batch.batchId}`} style={{ marginTop: 16 }}>
          <Typography.Text>Status: <strong>{batch.status}</strong> · {batch.fileName}</Typography.Text>
          {(batch.status === 'PROCESSING' || batch.status === 'PENDING') && (
            <Progress percent={pct} status="active" style={{ marginTop: 12 }} />
          )}
          {batch.status === 'FAILED' && batch.errorMessage && (
            <Alert type="error" message={batch.errorMessage} style={{ marginTop: 12 }} />
          )}
          {batch.summary && (
            <div style={{ display: 'flex', gap: 24, marginTop: 16, flexWrap: 'wrap' }}>
              <Statistic title="Accepted" value={batch.summary.accepted} />
              <Statistic title="PASS" value={batch.summary.pass} />
              <Statistic title="MONITOR" value={batch.summary.monitor} />
              <Statistic title="EXCEPTION" value={batch.summary.exception} />
              <Statistic title="REJECTED" value={batch.summary.rejected} />
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
