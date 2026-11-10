import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Calendar, X } from "lucide-react";
import DateCalendar from "../DateCalendar";
import { useLanguage } from "../../lib/i18n/LanguageContext";
import { formatFullDate } from "../../lib/i18n/dateFormat";

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function DatePickerField({ value, onChange, minDate, label }) {
  const { t } = useLanguage();
  const [open, setOpen] = useState(false);

  function formatDisplay(iso) {
    if (!iso) return t("picker.selectDate");
    const [y, m, d] = iso.split("-").map(Number);
    return formatFullDate(new Date(y, m - 1, d));
  }

  // Lock background scroll while the popup is open. Mobile Safari can
  // resize the visual viewport (its address bar collapsing/expanding) while
  // the page behind a `fixed` overlay is still scrollable, which shifts
  // where touches actually land relative to what's on screen — taps on the
  // popup's own controls then silently miss. Locking scroll keeps the
  // viewport stable for as long as the popup is up.
  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(e) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  return (
    <div className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={label || t("picker.chooseDate")}
        className="touch-manipulation flex items-center gap-2 rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-content transition-colors hover:border-accent/50 focus:border-accent focus:outline-none"
      >
        <Calendar size={16} className="text-accent" />
        {formatDisplay(value)}
      </button>

      {open &&
        createPortal(
          // Rendered through a portal (not as a DOM descendant of the call
          // site's wrapper) so a labelable ancestor can't re-dispatch taps on
          // these controls back to the trigger button. iOS Safari does exactly
          // that for interactive content nested inside a <label>, which made
          // the close button and backdrop immediately re-open the popup.
          <div
            role="dialog"
            aria-modal="true"
            aria-label={label || t("picker.chooseDate")}
            className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/85 px-4"
            onClick={() => setOpen(false)}
          >
            <div className="w-full max-w-xs shadow-2xl" onClick={(e) => e.stopPropagation()}>
              <div className="mb-2 flex justify-end">
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="touch-manipulation flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-content/80 shadow-lg transition-colors hover:border-accent/50 hover:text-accent"
                >
                  <X size={14} />
                  {t("picker.close")}
                </button>
              </div>
              <DateCalendar
                value={value || todayIso()}
                minDate={minDate}
                onChange={(iso) => {
                  onChange(iso);
                  setOpen(false);
                }}
              />
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}
