import { lazy } from 'react';

const RELOAD_KEY = 'cas_chunk_reload_attempted';

/**
 * True when an error looks like a stale dynamic-import chunk failure —
 * i.e. the browser is running an old app shell whose hashed chunk names
 * no longer exist on the server after a redeploy.
 */
function isStaleChunkError(err) {
  const msg = String(err?.message || err || '');
  return /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|dynamically imported module/i.test(
    msg,
  );
}

/**
 * Drop-in replacement for React.lazy that recovers from stale-chunk errors
 * after a new deployment. When a dynamic import fails because the hashed
 * chunk is gone, it triggers a one-time full page reload to pull the fresh
 * build instead of showing an error screen. The reload is guarded by a
 * session flag so a genuinely broken chunk can't cause an infinite loop.
 */
export function lazyWithReload(factory) {
  return lazy(async () => {
    try {
      const mod = await factory();
      // Loaded cleanly — clear the guard so a later redeploy in the same
      // session can still recover.
      try {
        sessionStorage.removeItem(RELOAD_KEY);
      } catch {
        /* ignore storage errors */
      }
      return mod;
    } catch (err) {
      let alreadyTried = false;
      try {
        alreadyTried = sessionStorage.getItem(RELOAD_KEY) === '1';
      } catch {
        /* ignore storage errors */
      }

      if (isStaleChunkError(err) && !alreadyTried) {
        try {
          sessionStorage.setItem(RELOAD_KEY, '1');
        } catch {
          /* ignore storage errors */
        }
        window.location.reload();
        // Keep Suspense in its loading state while the reload happens
        // (this promise intentionally never resolves).
        return new Promise(() => {});
      }

      // Not a stale-chunk error, or we already reloaded once — let the
      // error boundary handle it.
      throw err;
    }
  });
}
