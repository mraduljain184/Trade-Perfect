import axios from 'axios';

import { clearStoredSession, getStoredToken } from '@/api/storage';

function apiBaseUrl(): string {
  if (import.meta.env.DEV) {
    return '/api/v1';
  }
  const base = import.meta.env.VITE_API_BASE_URL;
  if (!base) {
    throw new Error('Set VITE_API_BASE_URL for production builds');
  }
  return `${base.replace(/\/$/, '')}/api/v1`;
}

const baseURL = apiBaseUrl();

export const apiClient = axios.create({
  baseURL,
  headers: { 'Content-Type': 'application/json' },
});

apiClient.interceptors.request.use((config) => {
  const token = getStoredToken();
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

apiClient.interceptors.response.use(
  (res) => res,
  (error) => {
    if (error.response?.status === 401 && getStoredToken()) {
      clearStoredSession();
      onUnauthorized?.();
    }
    return Promise.reject(error);
  },
);
