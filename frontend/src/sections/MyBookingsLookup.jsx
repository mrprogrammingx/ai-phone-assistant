import { useState } from "react";
import { motion } from "framer-motion";
import { Loader2, Search } from "lucide-react";
import { getMyBookings } from "../lib/api";
import { formatDateLabelLong, formatTimeLabel } from "../lib/i18n/dateFormat";
import { useLanguage } from "../lib/i18n/LanguageContext";

export default function MyBookingsLookup() {
  const { t } = useLanguage();
  const [phone, setPhone] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const bookings = await getMyBookings(phone);
      setResults(bookings);
    } catch (err) {
      setError(err.message);
      setResults(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl">
      <form onSubmit={handleSubmit} className="flex flex-col gap-3 sm:flex-row">
        <input
          type="tel"
          required
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder={t("myBookings.placeholder")}
          className="flex-1 rounded-lg border border-line bg-canvas px-4 py-3 text-content outline-none transition-colors focus:border-accent"
        />
        <button type="submit" disabled={loading} className="btn-outline shrink-0">
          {loading ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />}
          {t("myBookings.findButton")}
        </button>
      </form>

      {error && <p className="mt-4 text-sm text-red-700">{error}</p>}

      {results && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-6 flex flex-col gap-3"
        >
          {results.length === 0 && (
            <p className="text-sm text-muted">{t("myBookings.noResults")}</p>
          )}
          {results.map((entry) => (
            <div
              key={entry.id}
              className="flex items-center justify-between rounded-lg border border-line bg-canvas px-5 py-4"
            >
              <div>
                <p className="text-content">{formatDateLabelLong(entry.queue_date)}</p>
                <p className="text-sm text-muted">{formatTimeLabel(entry.appointment_time)}</p>
              </div>
              <span className="rounded-full border border-accent/30 px-3 py-1 text-xs text-accent">
                {t(`status.${entry.status}`)}
              </span>
            </div>
          ))}
        </motion.div>
      )}
    </div>
  );
}
