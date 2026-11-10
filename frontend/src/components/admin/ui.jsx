import { useLanguage } from "../../lib/i18n/LanguageContext";

export function Card({ title, children, className = "" }) {
  return (
    <section className={`rounded-lg border border-line bg-surface shadow-card p-6 ${className}`}>
      {title && <h2 className="mb-4 font-display text-lg text-accent">{title}</h2>}
      {children}
    </section>
  );
}

export const inputClass =
  "rounded-lg border border-line bg-canvas px-3 py-2 text-sm text-content focus:border-accent focus:outline-none";

export const buttonClass =
  "inline-flex items-center justify-center gap-2 rounded-md bg-accent px-5 py-2 text-xs font-semibold text-canvas transition-all hover:bg-accent-light disabled:cursor-not-allowed disabled:opacity-40";

export const buttonOutlineClass =
  "inline-flex items-center justify-center gap-2 rounded-md border border-accent px-5 py-2 text-xs font-semibold text-content transition-all hover:border-accent hover:bg-accent/10 disabled:cursor-not-allowed disabled:opacity-40";

export function Toggle({ checked, onChange, label, disabled = false }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-8 w-14 shrink-0 items-center rounded-full transition-colors duration-300 ease-out focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:cursor-not-allowed disabled:opacity-40 ${
        checked ? "bg-accent" : "bg-line"
      }`}
    >
      <span
        className={`inline-block h-6 w-6 transform rounded-full bg-white shadow-md transition-transform duration-300 ease-out ${
          checked ? "translate-x-7" : "translate-x-1"
        }`}
      />
    </button>
  );
}

const STATUS_STYLES = {
  waiting: "text-accent",
  next: "text-blue-700",
  called: "text-blue-700",
  in_progress: "text-blue-700",
  done: "text-emerald-700",
  no_show: "text-red-700",
  cancelled: "text-muted",
};

export function StatusPill({ status }) {
  const { t } = useLanguage();
  return (
    <span className={`text-sm font-medium capitalize ${STATUS_STYLES[status] || "text-content"}`}>
      {t(`adminStatus.${status}`)}
    </span>
  );
}

// Looks like a plain status pill, but is a native <select> underneath so tapping
// the status itself opens the change options — no separate action buttons needed.
export function StatusSelect({ status, onChange }) {
  const { t } = useLanguage();
  // A cancelled appointment has given up its slot, so staff cannot change it.
  if (status === "cancelled") return <StatusPill status={status} />;

  const actionable = ["done", "no_show"];
  const options = actionable.includes(status) ? actionable : [status, ...actionable];
  return (
    <select
      value={status}
      onChange={(e) => {
        if (e.target.value !== status) onChange(e.target.value);
      }}
      className={`cursor-pointer appearance-none bg-transparent text-right text-sm font-medium capitalize focus:outline-none ${
        STATUS_STYLES[status] || "text-content"
      }`}
    >
      {options.map((value) => (
        <option key={value} value={value} className="bg-surface text-content">
          {t(`adminStatus.${value}`)}
        </option>
      ))}
    </select>
  );
}

export function EntryCard({ title, subtitle, headerActions, children, footer }) {
  return (
    <div className="overflow-hidden rounded-lg border border-line bg-surface shadow-card">
      <div className="flex items-center justify-between gap-3 px-4 py-3">
        <div>
          <div className="font-semibold text-content">{title}</div>
          {subtitle && <div className="text-xs text-content/70">{subtitle}</div>}
        </div>
        {headerActions && <div className="flex items-center gap-2">{headerActions}</div>}
      </div>
      <div className="border-t border-line">{children}</div>
      {footer}
    </div>
  );
}

export function EntryRow({ label, children }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5 last:border-b-0">
      <span className="text-sm text-content/70">{label}</span>
      <span className="min-w-0 flex-1 text-right text-sm text-content">{children}</span>
    </div>
  );
}

export function Table({ columns, children }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {columns.map((col) => (
              <th key={col} className="border-b border-line px-3 py-2 text-left text-content/75">
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}
