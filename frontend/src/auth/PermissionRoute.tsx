import { Result } from 'antd';
import { Navigate } from 'react-router-dom';

import { useAuth } from '@/auth/AuthContext';

export function PermissionRoute({
  children,
  permission,
  anyOf,
}: {
  children: React.ReactNode;
  permission?: string;
  /** User needs at least one of these permissions */
  anyOf?: string[];
}) {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  const required = anyOf ?? (permission ? [permission] : []);
  if (required.length && !required.some((p) => user.permissions.includes(p))) {
    return <Result status="403" title="Access denied" subTitle="Your role cannot open this page." />;
  }
  return children;
}
