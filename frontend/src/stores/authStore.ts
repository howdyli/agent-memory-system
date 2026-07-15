import { create } from 'zustand';
import { authApi } from '../services/api';

interface User {
  id: number;
  username: string;
  email?: string;
}

interface AuthState {
  user: User | null;
  token: string | null;
  loading: boolean;
  initialized: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string, email?: string) => Promise<void>;
  logout: () => void;
  checkAuth: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: (() => { try { return JSON.parse(localStorage.getItem('user') || 'null'); } catch { return null; } })(),
  token: localStorage.getItem('access_token'),
  loading: false,
  initialized: false,

  login: async (username, password) => {
    set({ loading: true });
    try {
      const res = await authApi.login({ username, password });
      const { access_token, user_id, username: uname } = res.data;
      const user = { id: user_id, username: uname };
      localStorage.setItem('access_token', access_token);
      localStorage.setItem('user', JSON.stringify(user));
      set({ user, token: access_token, loading: false });
    } catch (e: unknown) {
      set({ loading: false });
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '登录失败';
      throw new Error(msg);
    }
  },

  register: async (username, password, email?) => {
    set({ loading: true });
    try {
      const res = await authApi.register({ username, password, email });
      const { access_token, user_id, username: uname } = res.data;
      const user = { id: user_id, username: uname };
      localStorage.setItem('access_token', access_token);
      localStorage.setItem('user', JSON.stringify(user));
      set({ user, token: access_token, loading: false });
    } catch (e: unknown) {
      set({ loading: false });
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '注册失败';
      throw new Error(msg);
    }
  },

  logout: () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    set({ user: null, token: null });
  },

  checkAuth: async () => {
    const token = get().token;
    if (!token) {
      set({ initialized: true });
      return;
    }
    try {
      const res = await authApi.me();
      const { user_id, username, email } = res.data;
      const user = { id: user_id, username, email };
      set({ user, initialized: true });
    } catch {
      localStorage.removeItem('access_token');
      localStorage.removeItem('user');
      set({ user: null, token: null, initialized: true });
    }
  },
}));
