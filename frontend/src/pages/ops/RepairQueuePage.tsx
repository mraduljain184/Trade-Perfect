import {

  Button,

  Card,

  Drawer,

  Form,

  Input,

  Modal,

  Select,

  Space,

  Statistic,

  Table,

  Tag,

  Tooltip,

  Typography,

  message,

} from 'antd';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { useMemo, useState } from 'react';

import { useNavigate, useSearchParams } from 'react-router-dom';



import { normalizeApiError } from '@/api/errors';

import { fetchUsers } from '@/api/reference';

import {

  approveQueueItem,

  assignQueue,

  getQueueItem,

  getQueueSummary,

  listQueueItems,

  rejectQueueItem,

  resubmitQueueItem,

} from '@/api/repairQueue';

import { useAuth } from '@/auth/AuthContext';

import { StatusTag } from '@/components/StatusTag';

import type { QueueItem, User } from '@/types/api';

import { fmtDate, fmtScore } from '@/utils/format';



const STATUS_OPTS = ['OPEN', 'IN_PROGRESS', 'RESOLVED', 'REJECTED'].map((s) => ({ value: s, label: s }));

const PRIORITY_OPTS = ['HIGH', 'MEDIUM', 'LOW'].map((s) => ({ value: s, label: s }));

const MIN_QUEUE_RISK = 0.5;



function canManageQueue(perms: string[]) {

  return perms.includes('FIX_APPLY') || perms.includes('QUEUE_ASSIGN');

}



function canAssignToOthers(user: User | null): boolean {

  if (!user) return false;

  return (

    user.role === 'OPS_MANAGER' ||

    user.permissions.includes('QUEUE_REASSIGN') ||

    user.roleName.toLowerCase().includes('manager')

  );

}



export function RepairQueuePage() {

  const { user } = useAuth();

  const navigate = useNavigate();

  const [searchParams, setSearchParams] = useSearchParams();

  const qc = useQueryClient();

  const readOnly = !user || !canManageQueue(user.permissions);

  const managerAssign = canAssignToOthers(user);



  const [page, setPage] = useState(1);

  const [status, setStatus] = useState<string | undefined>('OPEN,IN_PROGRESS');

  const [priority, setPriority] = useState<string | undefined>();

  const [assignedTo, setAssignedTo] = useState<string | undefined>();

  const [search, setSearch] = useState('');

  const [refreshing, setRefreshing] = useState(false);

  const [drawerId, setDrawerId] = useState<string | null>(searchParams.get('queueId'));

  const [rejectOpen, setRejectOpen] = useState(false);

  const [approveOpen, setApproveOpen] = useState(false);

  const [assignOpen, setAssignOpen] = useState(false);

  const [assignTarget, setAssignTarget] = useState<QueueItem | null>(null);

  const [activeItem, setActiveItem] = useState<QueueItem | null>(null);

  const [rejectForm] = Form.useForm();

  const [approveForm] = Form.useForm();

  const [assignForm] = Form.useForm();



  const listParams = {

    page,

    pageSize: 20,

    status,

    priority,

    assignedTo,

    search: search || undefined,

    minRiskScore: MIN_QUEUE_RISK,

  };



  const { data: summary } = useQuery({

    queryKey: ['queue-summary', MIN_QUEUE_RISK],

    queryFn: () => getQueueSummary({ minRiskScore: MIN_QUEUE_RISK }),

  });

  const { data, isLoading } = useQuery({

    queryKey: ['repair-queue', listParams],

    queryFn: () => listQueueItems(listParams),

  });



  const { data: drawerItem, isLoading: drawerLoading } = useQuery({

    queryKey: ['queue-item', drawerId],

    queryFn: () => getQueueItem(drawerId!),

    enabled: !!drawerId,

  });



  const opsUsers = useQuery({

    queryKey: ['users', 'OPS_USER'],

    queryFn: () => fetchUsers('OPS_USER'),

    enabled: managerAssign,

  });



  const invalidate = () => {

    void qc.invalidateQueries({ queryKey: ['repair-queue'] });

    void qc.invalidateQueries({ queryKey: ['queue-summary'] });

    if (drawerId) void qc.invalidateQueries({ queryKey: ['queue-item', drawerId] });

  };



  const handleRefresh = async () => {

    setRefreshing(true);

    try {

      await Promise.all([

        qc.refetchQueries({ queryKey: ['repair-queue'], type: 'active' }),

        qc.refetchQueries({ queryKey: ['queue-summary'], type: 'active' }),

      ]);

      message.success('Queue refreshed');

    } finally {

      setRefreshing(false);

    }

  };



  const resubmitMut = useMutation({

    mutationFn: resubmitQueueItem,

    onSuccess: () => {

      message.success('Trade re-submitted and queue item resolved');

      invalidate();

    },

    onError: (e) => message.error(normalizeApiError(e).message),

  });



  const rejectMut = useMutation({

    mutationFn: ({ id, reason }: { id: string; reason: string }) => rejectQueueItem(id, reason),

    onSuccess: () => {

      message.success('Item rejected');

      setRejectOpen(false);

      invalidate();

    },

    onError: (e) => message.error(normalizeApiError(e).message),

  });



  const approveMut = useMutation({

    mutationFn: ({ id, note }: { id: string; note: string }) => approveQueueItem(id, note),

    onSuccess: () => {

      message.success('High-priority item approved');

      setApproveOpen(false);

      invalidate();

    },

    onError: (e) => message.error(normalizeApiError(e).message),

  });



  const assignMut = useMutation({

    mutationFn: ({ ids, assigneeId }: { ids: string[]; assigneeId: number | null }) =>

      assignQueue(ids, assigneeId),

    onSuccess: (r) => {

      message.info(`${r.succeeded.length} assigned${r.failed.length ? `, ${r.failed.length} failed` : ''}`);

      setAssignOpen(false);

      assignForm.resetFields();

      invalidate();

    },

    onError: (e) => message.error(normalizeApiError(e).message),

  });



  const openDrawer = (queueId: string) => {

    setDrawerId(queueId);

    setSearchParams({ queueId });

  };



  const openAssign = (row: QueueItem) => {

    setAssignTarget(row);

    assignForm.setFieldsValue({ assigneeId: undefined });

    setAssignOpen(true);

  };



  const columns = useMemo(

    () => [

      { title: 'Queue ID', dataIndex: 'queueId', key: 'queueId', width: 110 },

      {

        title: 'Trade ID',

        dataIndex: 'tradeId',

        key: 'tradeId',

        render: (id: string) => (

          <Button type="link" size="small" onClick={() => navigate(`/trades/${id}`)}>

            {id}

          </Button>

        ),

      },

      { title: 'Priority', dataIndex: 'priority', key: 'priority', render: (p: string) => <Tag>{p}</Tag> },

      { title: 'Status', dataIndex: 'status', key: 'status' },

      { title: 'Risk', dataIndex: 'riskScore', key: 'riskScore', render: fmtScore },

      {

        title: 'Trade status',

        key: 'tstatus',

        render: (_: unknown, row: QueueItem) => <StatusTag status={row.trade.status} />,

      },

      {

        title: 'Assigned',

        key: 'assigned',

        render: (_: unknown, row: QueueItem) => row.assignedTo?.fullName ?? '—',

      },

      {

        title: 'Updated',

        dataIndex: 'updatedAt',

        key: 'updatedAt',

        render: (v: string) => fmtDate(v),

      },

      {

        title: '',

        key: 'actions',

        render: (_: unknown, row: QueueItem) => {

          const assignedToMe = row.assignedTo?.id === user?.id;

          const showManagerAssign =

            !readOnly && managerAssign && row.status === 'OPEN' && !row.assignedTo;

          const showOpsAssignedBadge =

            !readOnly && !managerAssign && assignedToMe && row.status === 'IN_PROGRESS';



          return (

            <Space size="small">

              <Button size="small" onClick={() => openDrawer(row.queueId)}>View</Button>

              {showManagerAssign && (

                <Button size="small" type="primary" className="tp-btn-bnp" onClick={() => openAssign(row)}>

                  Assign

                </Button>

              )}

              {showOpsAssignedBadge && <Tag color="success">Assigned</Tag>}

            </Space>

          );

        },

      },

    ],

    [managerAssign, navigate, readOnly, user],

  );



  const item = drawerItem ?? activeItem;



  return (

    <div>

      <Typography.Title level={4} style={{ marginTop: 0 }}>Repair queue</Typography.Title>

      <Typography.Paragraph type="secondary">

        Trades with risk score above {MIN_QUEUE_RISK} (MONITOR / EXCEPTION) need a settlement check.
        Totals are real queue rows in the database (often tens of thousands after large CSV uploads in dev).

        {readOnly && ' You can view items for your trades; operations staff apply fixes.'}

      </Typography.Paragraph>



      {summary && (

        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 16 }}>

          <Card size="small">
            <Statistic title="Total in queue" value={summary.total} formatter={(v) => Number(v).toLocaleString()} />
          </Card>

          {Object.entries(summary.byStatus).map(([k, v]) => (

            <Card size="small" key={k}>
              <Statistic title={k} value={v} formatter={(val) => Number(val).toLocaleString()} />
            </Card>

          ))}

        </div>

      )}



      <Card>

        <Space wrap style={{ marginBottom: 16 }}>

          <Select

            allowClear

            placeholder="Status"

            style={{ minWidth: 180 }}

            value={status?.split(',')}

            mode="multiple"

            options={STATUS_OPTS}

            onChange={(v) => {

              setPage(1);

              setStatus(v.length ? v.join(',') : undefined);

            }}

          />

          <Select

            allowClear

            placeholder="Priority"

            style={{ width: 140 }}

            options={PRIORITY_OPTS}

            value={priority}

            onChange={(v) => {

              setPage(1);

              setPriority(v);

            }}

          />

          {managerAssign && user?.permissions.includes('QUEUE_ASSIGN') && (

            <Select

              allowClear

              placeholder="Assigned to"

              style={{ width: 160 }}

              options={[

                { value: 'unassigned', label: 'Unassigned' },

                { value: 'me', label: 'Assigned to me' },

                ...(opsUsers.data?.map((u) => ({ value: String(u.id), label: u.fullName })) ?? []),

              ]}

              value={assignedTo}

              onChange={(v) => {

                setPage(1);

                setAssignedTo(v);

              }}

            />

          )}

          <Input.Search

            placeholder="Queue / trade / symbol"

            allowClear

            onSearch={(v) => {

              setPage(1);

              setSearch(v);

            }}

            style={{ width: 220 }}

          />

          <Button loading={refreshing} onClick={() => void handleRefresh()}>Refresh</Button>

        </Space>



        <Table

          rowKey="queueId"

          loading={isLoading || refreshing}

          dataSource={data?.items ?? []}

          columns={columns}

          pagination={{

            current: page,

            pageSize: 20,

            total: data?.total ?? 0,

            onChange: setPage,

            showSizeChanger: false,

          }}

        />

      </Card>



      <Drawer

        title={drawerId ? `Queue ${drawerId}` : 'Queue item'}

        width={520}

        open={!!drawerId}

        onClose={() => {

          setDrawerId(null);

          setSearchParams({});

        }}

        loading={drawerLoading}

      >

        {item && (

          <Space direction="vertical" size="middle" style={{ width: '100%' }}>

            <div>

              <Tag>{item.priority}</Tag>

              <Tag>{item.status}</Tag>

              <StatusTag status={item.trade.status} />

            </div>

            <Typography.Text>

              Trade <Button type="link" onClick={() => navigate(`/trades/${item.tradeId}`)}>{item.tradeId}</Button>

              · Risk {fmtScore(item.riskScore)}

            </Typography.Text>

            {!readOnly && (

              <Space wrap>

                {user?.permissions.includes('FIX_APPLY') && (

                  <Button onClick={() => navigate(`/trades/${item.tradeId}`, { state: { queueId: item.queueId } })}>

                    Apply fix

                  </Button>

                )}

                <Tooltip title={item.canResubmit ? '' : 'HIGH items must be fixed or approved first'}>

                  <Button

                    disabled={!item.canResubmit}

                    loading={resubmitMut.isPending}

                    onClick={() => resubmitMut.mutate(item.queueId)}

                  >

                    Re-submit

                  </Button>

                </Tooltip>

                {item.canReject && (

                  <Button danger onClick={() => { setActiveItem(item); setRejectOpen(true); }}>

                    Reject

                  </Button>

                )}

                {user?.permissions.includes('HIGH_RESOLUTION_APPROVE') && item.priority === 'HIGH' && item.status !== 'RESOLVED' && (

                  <Button type="primary" className="tp-btn-bnp" onClick={() => { setActiveItem(item); setApproveOpen(true); }}>

                    Approve

                  </Button>

                )}

              </Space>

            )}

          </Space>

        )}

      </Drawer>



      <Modal

        title={assignTarget ? `Assign ${assignTarget.queueId}` : 'Assign queue item'}

        open={assignOpen}

        onCancel={() => setAssignOpen(false)}

        onOk={() => assignForm.submit()}

        confirmLoading={assignMut.isPending}

        destroyOnClose

      >

        <Form

          form={assignForm}

          layout="vertical"

          onFinish={(v) =>

            assignTarget &&

            assignMut.mutate({ ids: [assignTarget.queueId], assigneeId: v.assigneeId as number })

          }

        >

          <Form.Item

            name="assigneeId"

            label="Operations user"

            rules={[{ required: true, message: 'Choose who should work this item' }]}

          >

            <Select

              placeholder="Ops User One, Two, or Three…"

              options={opsUsers.data?.map((u) => ({ value: u.id, label: u.fullName })) ?? []}

              loading={opsUsers.isLoading}

            />

          </Form.Item>

        </Form>

      </Modal>



      <Modal title="Reject queue item" open={rejectOpen} onCancel={() => setRejectOpen(false)} onOk={() => rejectForm.submit()} confirmLoading={rejectMut.isPending}>

        <Form form={rejectForm} layout="vertical" onFinish={(v) => activeItem && rejectMut.mutate({ id: activeItem.queueId, reason: v.reason })}>

          <Form.Item name="reason" label="Reason" rules={[{ required: true, min: 3 }]}>

            <Input.TextArea rows={3} />

          </Form.Item>

        </Form>

      </Modal>



      <Modal title="Approve resolution" open={approveOpen} onCancel={() => setApproveOpen(false)} onOk={() => approveForm.submit()} confirmLoading={approveMut.isPending}>

        <Form form={approveForm} layout="vertical" onFinish={(v) => activeItem && approveMut.mutate({ id: activeItem.queueId, note: v.note })}>

          <Form.Item name="note" label="Manager note" rules={[{ required: true, min: 3 }]}>

            <Input.TextArea rows={3} />

          </Form.Item>

        </Form>

      </Modal>

    </div>

  );

}


