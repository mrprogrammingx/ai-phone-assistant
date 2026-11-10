import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { buttonClass, buttonOutlineClass } from "./ui";
import { useLanguage } from "../../lib/i18n/LanguageContext";

const AdminUIContext = createContext(null);

export function AdminUIProvider({ children }) {
  const { t } = useLanguage();
  const [toasts, setToasts] = useState([]);
  const [confirmState, setConfirmState] = useState(null);

  const toast = useCallback((message) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { id, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3000);
  }, []);

  const confirm = useCallback((message) => {
    return new Promise((resolve) => {
      setConfirmState({ message, resolve });
    });
  }, []);

  function resolveConfirm(result) {
    confirmState?.resolve(result);
    setConfirmState(null);
  }

  // See DatePickerField for why: locks background scroll so mobile Safari's
  // viewport doesn't shift under a fixed overlay and throw off tap targets.
  useEffect(() => {
    if (!confirmState) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [confirmState]);

  return (
    <AdminUIContext.Provider value={{ toast, confirm }}>
      {children}

      <div className="pointer-events-none fixed inset-x-0 top-6 z-50 flex flex-col items-center gap-2 px-4">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="pointer-events-auto flex items-center gap-2 rounded-full border border-accent/40 bg-surface px-5 py-2.5 text-sm text-content shadow-2xl animate-[toast-in_0.25s_ease-out]"
          >
            <span className="flex h-4 w-4 items-center justify-center rounded-full bg-accent text-[10px] font-bold text-canvas">
              ✓
            </span>
            {t.message}
          </div>
        ))}
      </div>

      {confirmState && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/90 px-4 animate-[fade-in_0.15s_ease-out]">
          <div className="w-full max-w-sm rounded-lg border border-line bg-surface shadow-card p-6 shadow-2xl animate-[modal-in_0.2s_ease-out]">
            <p className="mb-6 text-sm leading-relaxed text-content">{confirmState.message}</p>
            <div className="flex justify-end gap-3">
              <button type="button" onClick={() => resolveConfirm(false)} className={buttonOutlineClass}>
                {t("admin.cancel")}
              </button>
              <button type="button" onClick={() => resolveConfirm(true)} className={buttonClass}>
                {t("admin.confirm")}
              </button>
            </div>
          </div>
        </div>
      )}

      <style>{`
        @keyframes toast-in { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes fade-in { from { opacity: 0; } to { opacity: 1; } }
        @keyframes modal-in { from { opacity: 0; transform: scale(0.96) translateY(4px); } to { opacity: 1; transform: scale(1) translateY(0); } }
      `}</style>
    </AdminUIContext.Provider>
  );
}

export function useAdminUI() {
  const ctx = useContext(AdminUIContext);
  if (!ctx) throw new Error("useAdminUI must be used within AdminUIProvider");
  return ctx;
}
