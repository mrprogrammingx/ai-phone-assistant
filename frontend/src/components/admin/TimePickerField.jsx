import { Clock } from "lucide-react";
import { useLanguage } from "../../lib/i18n/LanguageContext";

const HOURS = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, "0"));
const MINUTES = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, "0"));

const selectClass =
  "appearance-none rounded-md border border-line bg-canvas px-2 py-1.5 text-sm text-content transition-colors hover:border-accent/50 focus:border-accent focus:outline-none";

export default function TimePickerField({ value, onChange, required }) {
  const { t } = useLanguage();
  const [hh = "09", mm = "00"] = (value || "").split(":");

  function update(nextHh, nextMm) {
    onChange(`${nextHh}:${nextMm}`);
  }

  return (
    <div className="flex items-center gap-1.5 rounded-lg border border-line bg-surface shadow-card px-2.5 py-1">
      <Clock size={14} className="text-accent" />
      <select
        required={required}
        aria-label={t("picker.hour")}
        value={hh}
        onChange={(e) => update(e.target.value, mm)}
        className={selectClass}
      >
        {HOURS.map((h) => (
          <option key={h} value={h}>
            {h}
          </option>
        ))}
      </select>
      <span className="text-content/40">:</span>
      <select
        required={required}
        aria-label={t("picker.minute")}
        value={mm}
        onChange={(e) => update(hh, e.target.value)}
        className={selectClass}
      >
        {MINUTES.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
    </div>
  );
}
