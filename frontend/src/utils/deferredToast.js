import toast from 'react-hot-toast';

const STORAGE_KEY = 'cas_deferred_toast';

/** Queue a toast to show after the next page load (Streamlit notify_deferred parity). */
export function queueDeferredToast(message, type = 'success') {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ message, type }));
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
    const { message, type } = JSON.parse(raw);
    if (type === 'error') toast.error(message);
    else toast.success(message);
  } catch {
    sessionStorage.removeItem(STORAGE_KEY);
  }
}
