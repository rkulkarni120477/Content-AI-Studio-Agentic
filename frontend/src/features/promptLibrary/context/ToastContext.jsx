// Prompt Library toast — direct port of the standalone ToastContext (its own
// scoped #toast element, styled by the ported CSS). Kept self-contained so the
// feature's toast look/behaviour is identical and independent of the host.
import { createContext, useCallback, useContext, useMemo, useState } from 'react';

const ToastContext = createContext(null);

export function ToastProvider({ children }) {
  const [message, setMessage] = useState('');
  const [visible, setVisible] = useState(false);

  const show = useCallback((msg) => {
    setMessage(msg);
    setVisible(true);
    window.setTimeout(() => setVisible(false), 2800);
  }, []);

  const value = useMemo(() => ({ message, show }), [message, show]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div id="toast" className={visible ? 'show' : ''}>
        {message}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}
