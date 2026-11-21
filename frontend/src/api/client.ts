import axios, { type InternalAxiosRequestConfig } from 'axios';
import { message } from 'antd';
import { useAuth } from '../stores/auth';
import type { Data } from '../types';

export const authClient = axios.create({ baseURL: '/api/v1', withCredentials: true, timeout: 30000 });
export const api = axios.create({ baseURL: '/api/v1', withCredentials: true, timeout: 120000 });
authClient.defaults.headers.common['X-EduFlow-Request'] = '1';
api.defaults.headers.common['X-EduFlow-Request'] = '1';
let refreshing: Promise<void> | null = null;

export async function refreshSession(): Promise<void> {
  if (!refreshing) refreshing = authClient.post('/auth/refresh').then(({ data }) => {
    useAuth.getState().setSession(data.user, data.access_token);
  }).catch((error) => { useAuth.getState().clear(); throw error; }).finally(() => { refreshing = null; });
  return refreshing;
}
api.interceptors.request.use((config) => {
  const token = useAuth.getState().token;
  if (token) config.headers.Authorization = 'Bearer ' + token;
  if (config.method && !['get', 'head', 'options'].includes(config.method)) {
    config.headers['Idempotency-Key'] ??= crypto.randomUUID();
  }
  return config;
});
api.interceptors.response.use((response) => response, async (error) => {
  const config = error.config as InternalAxiosRequestConfig & { retried?: boolean };
  if (error.response?.status === 401 && config && !config.retried) {
    config.retried = true;
    try { await refreshSession(); return await api(config); } catch { /* Login view handles revoked sessions. */ }
  }
  if (error.response?.status !== 401) message.error(error.response?.data?.title || error.message || '请求失败');
  return Promise.reject(error);
});
export async function get<T = Data>(url: string, params?: Data): Promise<T> {
  return (await api.get<T>(url, { params })).data;
}
export async function post<T = Data>(url: string, body: Data = {}): Promise<T> {
  return (await api.post<T>(url, body)).data;
}
