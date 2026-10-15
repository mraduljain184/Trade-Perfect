import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import { AuthProvider } from '@/auth/AuthContext';

import { LoginPage } from './LoginPage';

vi.mock('@/api/auth', () => ({
  loginRequest: vi.fn(),
  fetchMe: vi.fn(),
  logoutRequest: vi.fn(),
}));

describe('LoginPage', () => {
  it('renders sign-in form', () => {
    render(
      <MemoryRouter>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </MemoryRouter>,
    );
    expect(screen.getByRole('button', { name: /sign in/i })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('priya.sharma')).toBeInTheDocument();
  });
});
