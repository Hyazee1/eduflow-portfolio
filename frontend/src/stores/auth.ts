import { create } from 'zustand';
import type { User } from '../types';

interface AuthState {
  user: User | null;
  token: string | null;
  initialized: boolean;
  setSession: (user: User, token: string) => void;
  clear: () => void;
  ready: () => void;
}
export const useAuth = create<AuthState>((set) => ({
  user: null, token: null, initialized: false,
  setSession: (user, token) => set({ user, token, initialized: true }),
  clear: () => set({ user: null, token: null, initialized: true }),
  ready: () => set({ initialized: true }),
}));
