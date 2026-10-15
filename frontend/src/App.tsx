import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, Spin } from 'antd';
import { BrowserRouter } from 'react-router-dom';

import { AuthProvider, useAuth } from '@/auth/AuthContext';
import { AppRoutes } from '@/routes';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function AppShell() {
  const { loading } = useAuth();

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Spin size="large" />
      </div>
    );
  }

  return <AppRoutes />;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider theme={{ token: { colorPrimary: '#00965e', borderRadius: 6 } }}>
        <BrowserRouter>
          <AuthProvider>
            <AppShell />
          </AuthProvider>
        </BrowserRouter>
      </ConfigProvider>
    </QueryClientProvider>
  );
}
