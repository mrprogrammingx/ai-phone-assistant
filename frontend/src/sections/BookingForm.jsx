import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Calendar, CheckCircle2, Clock, Loader2, PartyPopper } from "lucide-react";
import { getAvailableTimes, checkIn } from "../lib/api";
import DateCalendar from "../components/DateCalendar";
import { todayISO } from "../lib/format";
import { formatDateLabelLong, formatTimeLabel } from "../lib/i18n/dateFormat";
import { useLanguage } from "../lib/i18n/LanguageContext";

// The same rule as validate_phone in app/schemas.py. The backend still decides.
function isValidPhone(value) {
  const phone = value.trim();
  const digits = phone.replace(/\D/g, "").length;
  return /^\+?[0-9\s\-()]{7,20}$/.test(phone) && digits >= 7;
}

export default function BookingForm() {
  const { t } = useLanguage();
  const [date, setDate] = useState(todayISO());
  const [slots, setSlots] = useState([]);
  const [slotsLoading, setSlotsLoading] = useState(true);
  const [slotsError, setSlotsError] = useState(null);

  const [selectedTime, setSelectedTime] = useState(null);
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [note, setNote] = useState("");
  const [nameBlank, setNameBlank] = useState(false);
  const [phoneInvalid, setPhoneInvalid] = useState(false);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(null);
  const [confirmation, setConfirmation] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setSlotsLoading(true);
    setSlotsError(null);
    setSelectedTime(null);

    getAvailableTimes(date)
      .then((data) => {
        if (!cancelled) setSlots(data);
      })
      .catch((err) => {
        if (!cancelled) setSlotsError(err.message);
      })
      .finally(() => {
        if (!cancelled) setSlotsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [date]);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!selectedTime) return;
    setSubmitError(null);
    // The same rules as app/schemas.py. The backend still decides.
    const blank = name.trim() === "";
    const invalid = !isValidPhone(phone);
    setNameBlank(blank);
    setPhoneInvalid(invalid);
    if (blank || invalid) return;
    setSubmitting(true);

    try {
      const entry = await checkIn({
        name,
        phone,
        appointmentDate: date,
        appointmentTime: selectedTime,
        note,
      });
      setConfirmation(entry);
    } catch (err) {
      setSubmitError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  function bookAnother() {
    setConfirmation(null);
    setName("");
    setPhone("");
    setNote("");
    setSelectedTime(null);
  }

  if (confirmation) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="mx-auto max-w-lg rounded-lg border border-accent/30 bg-surface p-10 text-center"
      >
        <motion.div
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={{ type: "spring", stiffness: 200, damping: 12, delay: 0.1 }}
          className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-full bg-accent/15 text-accent"
        >
          <PartyPopper size={30} />
        </motion.div>
        <h3 className="font-display text-3xl text-content">
          {t("bookingForm.youAreBooked", { name: confirmation.customer.name.split(" ")[0] })}
        </h3>
        <p className="mt-3 text-muted">
          {formatDateLabelLong(confirmation.queue_date)} · {formatTimeLabel(confirmation.appointment_time)}
        </p>
        <p className="mt-1 text-sm text-muted">{t("bookingForm.queuePosition", { n: confirmation.position })}</p>
        <button type="button" onClick={bookAnother} className="btn-outline mt-8">
          {t("bookingForm.bookAnother")}
        </button>
      </motion.div>
    );
  }

  return (
    <div className="mx-auto grid max-w-4xl grid-cols-1 gap-10 rounded-lg border border-line bg-surface shadow-card p-6 md:grid-cols-[1fr_1.1fr] md:p-10">
      <div>
        <label className="eyebrow mb-3 flex items-center gap-2">
          <Calendar size={14} /> {t("bookingForm.chooseDate")}
        </label>
        <DateCalendar value={date} onChange={setDate} minDate={todayISO()} />

        <label className="eyebrow mb-3 mt-8 flex items-center gap-2">
          <Clock size={14} /> {t("bookingForm.availableTimes")}
        </label>

        {slotsLoading && (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Loader2 size={16} className="animate-spin" /> {t("bookingForm.loadingTimes")}
          </div>
        )}

        {slotsError && <p className="text-sm text-red-700">{slotsError}</p>}

        {!slotsLoading && !slotsError && slots.length === 0 && (
          <p className="text-sm text-muted">{t("bookingForm.closedOnDate")}</p>
        )}

        {!slotsLoading && !slotsError && slots.length > 0 && (
          <div className="grid grid-cols-3 gap-2">
            {slots.map((slot) => {
              const disabled = !slot.available;
              const isSelected = selectedTime === slot.time;
              return (
                <button
                  key={slot.time}
                  type="button"
                  disabled={disabled}
                  onClick={() => setSelectedTime(slot.time)}
                  className={`rounded-lg border px-3 py-2.5 text-sm font-medium transition-all ${
                    isSelected
                      ? "border-accent bg-accent text-canvas"
                      : disabled
                      ? "cursor-not-allowed border-line text-muted/60 line-through"
                      : "border-line text-content/90 hover:border-accent hover:text-accent"
                  }`}
                >
                  {formatTimeLabel(slot.time)}
                </button>
              );
            })}
          </div>
        )}
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        <div>
          <label htmlFor="name" className="eyebrow mb-2 block">
            {t("bookingForm.fullName")}
          </label>
          <input
            id="name"
            required
            value={name}
            onChange={(e) => {
              setName(e.target.value);
              setNameBlank(false);
            }}
            placeholder={t("bookingForm.fullNamePlaceholder")}
            aria-invalid={nameBlank}
            aria-describedby={nameBlank ? "name-error" : undefined}
            className="w-full rounded-lg border border-line bg-canvas px-4 py-3 text-content outline-none transition-colors focus:border-accent"
          />
          {nameBlank && (
            <p id="name-error" className="mt-2 text-sm text-red-700">
              {t("bookingForm.blankName")}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="phone" className="eyebrow mb-2 block">
            {t("bookingForm.phoneNumber")}
          </label>
          <input
            id="phone"
            required
            type="tel"
            value={phone}
            onChange={(e) => {
              setPhone(e.target.value);
              setPhoneInvalid(false);
            }}
            placeholder={t("bookingForm.phonePlaceholder")}
            aria-invalid={phoneInvalid}
            aria-describedby={phoneInvalid ? "phone-error" : undefined}
            className="w-full rounded-lg border border-line bg-canvas px-4 py-3 text-content outline-none transition-colors focus:border-accent"
          />
          {phoneInvalid && (
            <p id="phone-error" className="mt-2 text-sm text-red-700">
              {t("bookingForm.invalidPhone")}
            </p>
          )}
        </div>

        <div>
          <label htmlFor="note" className="eyebrow mb-2 block">
            {t("bookingForm.noteLabel")}
          </label>
          <textarea
            id="note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={t("bookingForm.notePlaceholder")}
            rows={3}
            className="w-full resize-none rounded-lg border border-line bg-canvas px-4 py-3 text-content outline-none transition-colors focus:border-accent"
          />
        </div>

        <AnimatePresence>
          {submitError && (
            <motion.p
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="text-sm text-red-700"
            >
              {submitError}
            </motion.p>
          )}
        </AnimatePresence>

        <button
          type="submit"
          disabled={!selectedTime || submitting}
          className="btn-primary mt-2 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {submitting ? (
            <>
              <Loader2 size={16} className="animate-spin" /> {t("bookingForm.booking")}
            </>
          ) : (
            <>
              <CheckCircle2 size={16} /> {t("bookingForm.confirmAppointment")}
            </>
          )}
        </button>
      </form>
    </div>
  );
}
