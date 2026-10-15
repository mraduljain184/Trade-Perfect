import { Navigate, Route, Routes } from 'react-router-dom';



import { useAuth } from '@/auth/AuthContext';

import { defaultRouteForRole } from '@/auth/defaultRoute';

import { PermissionRoute } from '@/auth/PermissionRoute';

import { ProtectedRoute } from '@/auth/ProtectedRoute';

import { AppLayout } from '@/layouts/AppLayout';

import { AuditTrailPage } from '@/pages/audit/AuditTrailPage';
import { SettlementChatPage } from '@/pages/chat/SettlementChatPage';

import { LoginPage } from '@/pages/LoginPage';

import { MlMonitoringPage } from '@/pages/ml/MlMonitoringPage';

import { DashboardPage } from '@/pages/ops/DashboardPage';

import { RepairQueuePage } from '@/pages/ops/RepairQueuePage';

import { BulkUploadPage } from '@/pages/trades/BulkUploadPage';

import { MyTradesPage } from '@/pages/trades/MyTradesPage';

import { TradeBookingPage } from '@/pages/trades/TradeBookingPage';

import { TradeDetailPage } from '@/pages/trades/TradeDetailPage';



function RoleHomeRedirect() {

  const { user } = useAuth();

  if (!user) return <Navigate to="/login" replace />;

  return <Navigate to={defaultRouteForRole(user.role)} replace />;

}



export function AppRoutes() {

  return (

    <Routes>

      <Route path="/login" element={<LoginPage />} />

      <Route

        element={

          <ProtectedRoute>

            <AppLayout />

          </ProtectedRoute>

        }

      >

        <Route

          path="/trades/new"

          element={

            <PermissionRoute permission="TRADE_SUBMIT_SINGLE">

              <TradeBookingPage />

            </PermissionRoute>

          }

        />

        <Route

          path="/trades/upload"

          element={

            <PermissionRoute permission="TRADE_SUBMIT_BULK">

              <BulkUploadPage />

            </PermissionRoute>

          }

        />

        <Route

          path="/my-trades"

          element={

            <PermissionRoute permission="TRADE_VIEW">

              <MyTradesPage />

            </PermissionRoute>

          }

        />

        <Route

          path="/trades/:tradeId"

          element={

            <PermissionRoute permission="TRADE_VIEW">

              <TradeDetailPage />

            </PermissionRoute>

          }

        />

        <Route

          path="/dashboard"

          element={

            <PermissionRoute anyOf={['EXCEPTION_DASHBOARD_VIEW', 'TRADE_VIEW']}>

              <DashboardPage />

            </PermissionRoute>

          }

        />

        <Route

          path="/repair-queue"

          element={

            <PermissionRoute anyOf={['REPAIR_QUEUE_MANAGE', 'TRADE_VIEW']}>

              <RepairQueuePage />

            </PermissionRoute>

          }

        />

        <Route

          path="/audit"

          element={

            <PermissionRoute anyOf={['AUDIT_VIEW', 'TRADE_VIEW']}>

              <AuditTrailPage />

            </PermissionRoute>

          }

        />

        <Route

          path="/chat"

          element={

            <PermissionRoute permission="CHATBOT_USE">

              <SettlementChatPage />

            </PermissionRoute>

          }

        />

        <Route

          path="/ml/monitoring"

          element={

            <PermissionRoute anyOf={['MODEL_METRICS_VIEW', 'HYBRID_WEIGHTS_CONFIG']}>

              <MlMonitoringPage />

            </PermissionRoute>

          }

        />

      </Route>

      <Route path="/" element={<RoleHomeRedirect />} />

      <Route path="*" element={<Navigate to="/login" replace />} />

    </Routes>

  );

}


