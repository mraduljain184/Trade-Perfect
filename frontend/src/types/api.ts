export type RoleCode = 'TRADER' | 'OPS_USER' | 'OPS_MANAGER' | 'ML_ADMIN' | 'SYS_ADMIN';
export type TradeStatus = 'PASS' | 'MONITOR' | 'EXCEPTION' | 'REJECTED';
export type Priority = 'HIGH' | 'MEDIUM' | 'LOW';

export interface User {
  id: number;
  username: string;
  fullName: string;
  email: string;
  role: RoleCode;
  roleName: string;
  permissions: string[];
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  accessToken: string;
  tokenType: string;
  expiresIn: number;
  user: User;
}

export interface FieldError {
  field: string;
  ruleId?: string | null;
  message: string;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  fields?: FieldError[];
}

export interface ApiErrorBody {
  error: ApiErrorDetail;
}

export interface SymbolRef {
  symbol: string;
}

export interface CounterpartyRef {
  name: string;
  riskTier: number;
  tierLabel: string;
}

export interface AccountRef {
  accountId: string;
  isActive: boolean;
}

export interface TradeInput {
  tradeId?: string;
  tradeType: 'BUY' | 'SELL';
  symbol: string;
  quantity: number;
  price: number;
  counterparty: string;
  tradeDate: string;
  settlementDate: string;
  accountId: string;
  ssiMatched: boolean;
  currencyMatched: boolean;
}

export interface ValidationResult {
  valid: boolean;
  fields: FieldError[];
  warnings: string[];
}

export interface RiskFactor {
  factor: string;
  pct: number;
  contribution: number;
  detail: string;
}

export interface RejectionReason {
  ruleId: string;
  field?: string;
  message: string;
}

export interface TradeResult {
  id: string;
  tradeId: string | null;
  tradeType: string | null;
  symbol: string | null;
  quantity: number | null;
  price: number | null;
  counterparty: string | null;
  tradeDate: string | null;
  settlementDate: string | null;
  accountId: string | null;
  ssiMatched: boolean | null;
  currencyMatched: boolean | null;
  traderName: string;
  source: string;
  status: TradeStatus;
  priority: Priority | null;
  ruleScore: number | null;
  mlScore: number | null;
  finalScore: number;
  riskFactors: RiskFactor[];
  likelyReason: string | null;
  suggestedAction: string | null;
  explanation: string | null;
  rejectionReasons: RejectionReason[] | null;
  queueId: string | null;
  queueStatus: string | null;
  version: number;
  createdAt: string;
  updatedAt: string;
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface UploadSummary {
  accepted: number;
  pass: number;
  monitor: number;
  exception: number;
  rejected: number;
}

export interface SuggestedFix {
  factor: string;
  fix: string;
}

export interface QueueAssignee {
  id: number;
  fullName: string;
}

export interface QueueItem {
  queueId: string;
  tradeId: string | null;
  trade: TradeResult;
  priority: Priority;
  status: 'OPEN' | 'IN_PROGRESS' | 'RESOLVED' | 'REJECTED';
  riskScore: number;
  suggestedFixes: SuggestedFix[];
  assignedTo: QueueAssignee | null;
  assignedAt: string | null;
  appliedFix: string | null;
  appliedBy: string | null;
  appliedAt: string | null;
  canResubmit: boolean;
  canReject: boolean;
  createdAt: string;
  updatedAt: string;
}

export interface QueueSummary {
  total: number;
  byStatus: Record<string, number>;
  byPriority: Record<string, number>;
}

export interface BulkFailure {
  id: string;
  reason: string;
}

export interface BulkResult {
  succeeded: string[];
  failed: BulkFailure[];
}

export interface AuditEvent {
  id: number;
  eventTime: string;
  actorUsername: string;
  actorRole: string | null;
  action: string;
  entityType: string;
  tradeId: string | null;
  queueId: string | null;
  riskScore: number | null;
  statusAfter: string | null;
  likelyReason: string | null;
  recommendedFix: string | null;
  appliedFix: string | null;
  details: Record<string, unknown> | null;
}

export interface AuditBox {
  tradeId: string | null;
  queueId: string | null;
  riskScore: number;
  likelyReason: string | null;
  recommendedFix: string | null;
  appliedFix: string | null;
  appliedBy: string | null;
  appliedTimestamp: string | null;
  status: string;
}

export interface TradeDetail extends TradeResult {
  versionsCount: number;
  auditBox: AuditBox;
  ruleHits?: { ruleId: string; category: string; triggered: boolean; contribution: number; message: string }[];
}

export interface TradeVersion {
  version: number;
  changeType: string;
  changedFields: Record<string, { old: unknown; new: unknown }> | null;
  changeReason: string | null;
  changedBy: string | null;
  changedAt: string;
  snapshot: Record<string, unknown>;
}

export interface DashboardSummary {
  today: { total: number; pass: number; monitor: number; exception: number; rejected: number };
  historical: { total: number; pass: number; monitor: number; exception: number; rejected: number };
}

export interface UserBrief {
  id: number;
  username: string;
  fullName: string;
  role: RoleCode;
}

export interface UploadBatch {
  batchId: string;
  fileName: string;
  status: 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED';
  totalRows: number;
  processedRows: number;
  summary: UploadSummary;
  startedAt: string;
  completedAt: string | null;
  errorMessage: string | null;
}
