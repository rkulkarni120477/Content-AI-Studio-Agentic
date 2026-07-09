import { useCallback, useState } from 'react';

const STORAGE_KEY = 'cas.sidebarCollapsed';

/**
 * Studio-wide sidebar collapse preference. One shared localStorage key so the
 * choice follows the user across the Selection, Workspace and Prompts layouts
 * (each layout mounts a single sidebar, which re-reads the key on mount).
 */
export function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(STORAGE_KEY) === '1';
    } catch {
      return false;
    }
  });

  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(STORAGE_KEY, next ? '1' : '0');
      } catch {
        /* storage unavailable (private mode) — collapse still works per-page */
      }
      return next;
    });
  }, []);

  return [collapsed, toggleCollapsed];
}
