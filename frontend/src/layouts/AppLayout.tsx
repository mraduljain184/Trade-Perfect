import { BellOutlined, LogoutOutlined } from '@ant-design/icons';
import { Badge, Button, Layout, Menu, Space, Tag, Typography } from 'antd';
import dayjs from 'dayjs';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';

import { useAuth } from '@/auth/AuthContext';
import { navForUser } from '@/config/nav';

const { Header, Sider, Content } = Layout;

export function AppLayout() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const nav = navForUser(user);
  const menuItems = nav.map((item) => ({
    key: item.path,
    icon: item.icon,
    label: item.label,
  }));

  const selected =
    nav.find((item) => location.pathname === item.path || location.pathname.startsWith(`${item.path}/`))?.path
    ?? menuItems[0]?.key;

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider width={240} theme="light" className="tp-app-sider">
        <div className="tp-sider-brand tp-sider-brand-bnp">
          <img
            src="/bnp-paribas-logo.png"
            alt="BNP Paribas"
            className="tp-bnp-logo-img"
          />
          <div>
            <Typography.Text strong className="tp-sider-brand-title">
              BNP PARIBAS
            </Typography.Text>
            <Typography.Text className="tp-sider-brand-sub">
              TradePerfect · settlement checks
            </Typography.Text>
          </div>
        </div>
        <div className="tp-sider-menu-wrap">
          <Menu
            mode="inline"
            selectedKeys={selected ? [selected] : []}
            items={menuItems}
            className="tp-sider-menu"
            onClick={({ key }) => navigate(String(key))}
          />
        </div>
      </Sider>
      <Layout>
        <Header className="tp-app-header">
          <Typography.Text type="secondary">
            Business date: {dayjs().format('YYYY-MM-DD')}
          </Typography.Text>
          {user && (
            <Space size="large">
              <Badge count={0} showZero={false}>
                <BellOutlined style={{ fontSize: 18 }} />
              </Badge>
              <Space>
                <Typography.Text strong>{user.fullName}</Typography.Text>
                <Tag color="green">{user.roleName}</Tag>
              </Space>
              <Button type="text" icon={<LogoutOutlined />} onClick={handleLogout}>Log out</Button>
            </Space>
          )}
        </Header>
        <Content style={{ padding: 24, background: '#f5f7fa', minHeight: 360 }}>
          <div style={{ maxWidth: 1200, margin: '0 auto' }}>
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}
