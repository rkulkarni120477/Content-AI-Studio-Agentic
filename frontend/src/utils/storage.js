import { STORAGE_KEYS } from './constants';

const storage = {
  get(key) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  },

  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      // Storage quota exceeded or private browsing — silently fail
    }
  },

  remove(key) {
    localStorage.removeItem(key);
  },

  clear() {
    Object.values(STORAGE_KEYS).forEach((k) => localStorage.removeItem(k));
  },
};

// Token-specific helpers
export const tokenStorage = {
  get: ()          => localStorage.getItem(STORAGE_KEYS.TOKEN),
  set: (token)     => localStorage.setItem(STORAGE_KEYS.TOKEN, token),
  remove: ()       => localStorage.removeItem(STORAGE_KEYS.TOKEN),
};

export default storage;
