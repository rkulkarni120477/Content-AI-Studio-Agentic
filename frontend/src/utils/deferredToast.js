import toast from 'react-hot-toast';

const STORAGE_KEY = 'cas_deferred_toast';

/**
 * Queue a toast to show after the next page load (Streamlit notify_deferred parity).
 * Pass { localized: true } for a message already built from the tenant labels.
 */
export function queueDeferredToast(message, type = 'success', { localized = false } = {}) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ message, type, localized }));
  } catch {
    /* ignore quota errors */
  }
}

/** Flush any queued toast — call once on app/layout mount. */
export function flushDeferredToasts() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    sessionStorage.removeItem(STORAGE_KEY);
    const { message, type, localized } = JSON.parse(raw);
    const opts = localized ? { localized: true } : undefined;
    if (type === 'error') toast.error(message, opts);
    else toast.success(message, opts);
  } catch {
    sessionStorage.removeItem(STORAGE_KEY);
  }
}
