import { useEffect, useState } from "react";
import { Check } from "lucide-react";
import {
  addIncludedSlot,
  blockSlot,
  getAvailableTimesAdmin,
  getShopStatus,
  removeIncludedSlot,
  setNotificationEmails,
  setOpenDays,
  setScheduleSettings,
  setShopStatus,
  unblockSlot,
} from "../../lib/adminApi";
import { Card, Toggle, buttonClass, buttonOutlineClass, inputClass } from "../../components/admin/ui";
import DatePickerField from "../../components/admin/DatePickerField";
import TimePickerField from "../../components/admin/TimePickerField";
import { useAdminUI } from "../../components/admin/AdminUIContext";
import { useLanguage } from "../../lib/i18n/LanguageContext";

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const DAY_OPTIONS = [
  { value: "mon", key: "day.mon" },
  { value: "tue", key: "day.tue" },
  { value: "wed", key: "day.wed" },
  { value: "thu", key: "day.thu" },
  { value: "fri", key: "day.fri" },
  { value: "sat", key: "day.sat" },
  { value: "sun", key: "day.sun" },
];

export default function AdminSettings() {
  const { t } = useLanguage();
  const { toast } = useAdminUI();
  const [status, setStatus] = useState(null);
  const [openDays, setOpenDaysState] = useState([]);
  const [schedule, setSchedule] = useState({ open_time: "", close_time: "", slot_duration_minutes: 30, capacity_per_slot: 1 });
  const [emailsText, setEmailsText] = useState("");
  const [emailsMessage, setEmailsMessage] = useState("");
  // The server's refusal, shown under the form it belongs to.
  const [daysError, setDaysError] = useState("");
  const [scheduleError, setScheduleError] = useState("");
  const [slotsError, setSlotsError] = useState("");
  const [blockDate, setBlockDate] = useState(todayIso());
  const [slots, setSlots] = useState([]);
  const [newTime, setNewTime] = useState("09:00");

  async function refreshStatus() {
    const data = await getShopStatus();
    setStatus(data);
    setOpenDaysState(data.open_days);
    setSchedule({
      open_time: data.open_time.slice(0, 5),
      close_time: data.close_time.slice(0, 5),
      slot_duration_minutes: data.slot_duration_minutes,
      capacity_per_slot: data.capacity_per_slot,
    });
    setEmailsText(data.notification_emails.join("\n"));
  }

  async function refreshSlots() {
    try {
      const data = await getAvailableTimesAdmin(blockDate);
      setSlots(data);
    } catch (err) {
      setSlots([]);
      // The server (not the browser clock, which can be skewed in some
      // environments) is the source of truth for "today" — if it rejects
      // this date as past, step forward a day and retry rather than
      // trusting a possibly-wrong client-side todayIso().
      if (/past/i.test(err.message || "")) {
        const [y, m, d] = blockDate.split("-").map(Number);
        const next = new Date(y, m - 1, d + 1);
        const iso = `${next.getFullYear()}-${String(next.getMonth() + 1).padStart(2, "0")}-${String(next.getDate()).padStart(2, "0")}`;
        setBlockDate(iso);
      }
    }
  }

  useEffect(() => {
    refreshStatus();
  }, []);

  useEffect(() => {
    refreshSlots();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blockDate]);

  async function toggleBookingToday() {
    await setShopStatus({ booking_open: !status.booking_open });
    refreshStatus();
  }

  function toggleDay(value) {
    setOpenDaysState((prev) => (prev.includes(value) ? prev.filter((d) => d !== value) : [...prev, value]));
  }

  async function saveOpenDays(e) {
    e.preventDefault();
    setDaysError("");
    try {
      await setOpenDays(openDays);
      toast(t("admin.settings.daysOpenSaved"));
    } catch (err) {
      setDaysError(err.message);
    }
  }

  async function saveSchedule(e) {
    e.preventDefault();
    setScheduleError("");
    try {
      await setScheduleSettings({
        open_time: schedule.open_time,
        close_time: schedule.close_time,
        slot_duration_minutes: Number(schedule.slot_duration_minutes),
        capacity_per_slot: Number(schedule.capacity_per_slot),
      });
      toast(t("admin.settings.slotSettingsSaved"));
      refreshSlots();
    } catch (err) {
      setScheduleError(err.message);
    }
  }

  async function saveEmails(e) {
    e.preventDefault();
    const notification_emails = emailsText
      .split(/[\n,]/)
      .map((email) => email.trim())
      .filter((email) => email.length > 0);
    try {
      await setNotificationEmails(notification_emails);
      setEmailsMessage(t("admin.settings.emailsSaved"));
    } catch (err) {
      setEmailsMessage(t("admin.settings.emailsError", { message: err.message }));
    }
  }

  async function handleSlotClick(slot) {
    setSlotsError("");
    try {
      if (slot.included) {
        await removeIncludedSlot(blockDate, slot.time);
      } else if (slot.blocked) {
        await unblockSlot(blockDate, slot.time);
      } else {
        await blockSlot(blockDate, slot.time);
      }
    } catch (err) {
      setSlotsError(err.message);
    }
    refreshSlots();
  }

  async function addExtraTime(e) {
    e.preventDefault();
    if (!newTime) return;
    setSlotsError("");
    try {
      await addIncludedSlot(blockDate, newTime);
    } catch (err) {
      setSlotsError(err.message);
    }
    refreshSlots();
  }

  if (!status) return <p className="text-content/70">{t("admin.settings.loading")}</p>;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="font-display text-2xl">{t("admin.settings.title")}</h1>

      <Card title={t("admin.settings.bookingToday")}>
        <p className="mb-4 max-w-md text-sm text-content/75">{t("admin.settings.bookingTodayDesc")}</p>
        <div className="flex items-center gap-4">
          <Toggle checked={status.booking_open} onChange={toggleBookingToday} label={t("admin.settings.toggleAria")} />
          <span className={`text-sm font-medium ${status.booking_open ? "text-accent" : "text-content/70"}`}>
            {status.booking_open ? t("admin.settings.bookingOpenToday") : t("admin.settings.bookingClosedToday")}
          </span>
        </div>
      </Card>

      <Card title={t("admin.settings.daysOpen")}>
        <form onSubmit={saveOpenDays} className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-2.5">
            {DAY_OPTIONS.map((day) => {
              const active = openDays.includes(day.value);
              return (
                <label
                  key={day.value}
                  className={`cursor-pointer select-none rounded-md border px-4 py-2 text-xs font-semibold transition-all duration-200 ${
                    active
                      ? "border-accent bg-accent text-canvas"
                      : "border-line bg-canvas text-content/75 hover:border-accent/50 hover:text-content"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={active}
                    onChange={() => toggleDay(day.value)}
                    className="sr-only"
                  />
                  {t(day.key)}
                </label>
              );
            })}
          </div>
          <div className="mt-1 flex items-center gap-3 border-t border-line pt-4">
            <button type="submit" className={`${buttonOutlineClass} gap-2`}>
              <Check size={14} />
              {t("admin.settings.saveDaysOpen")}
            </button>
          </div>
          {daysError && (
            <p role="alert" className="text-sm text-red-700">
              {daysError}
            </p>
          )}
        </form>
      </Card>

      <Card title={t("admin.settings.appointmentSlots")}>
        <form onSubmit={saveSchedule} className="flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-end">
          <label className="flex flex-col gap-1 text-sm text-content/70">
            {t("admin.settings.open")}
            <TimePickerField
              required
              value={schedule.open_time}
              onChange={(time) => setSchedule((s) => ({ ...s, open_time: time }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-content/70">
            {t("admin.settings.close")}
            <TimePickerField
              required
              value={schedule.close_time}
              onChange={(time) => setSchedule((s) => ({ ...s, close_time: time }))}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-content/70">
            {t("admin.settings.slotLength")}
            <input
              type="number"
              min="5"
              max="240"
              step="5"
              required
              value={schedule.slot_duration_minutes}
              onChange={(e) => setSchedule((s) => ({ ...s, slot_duration_minutes: e.target.value }))}
              className={inputClass}
            />
          </label>
          <label className="flex flex-col gap-1 text-sm text-content/70">
            {t("admin.settings.capacityPerSlot")}
            <input
              type="number"
              min="1"
              max="20"
              step="1"
              required
              value={schedule.capacity_per_slot}
              onChange={(e) => setSchedule((s) => ({ ...s, capacity_per_slot: e.target.value }))}
              className={inputClass}
            />
          </label>
          <button type="submit" className={buttonClass}>
            {t("admin.settings.saveSlotSettings")}
          </button>
        </form>
        {scheduleError && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            {scheduleError}
          </p>
        )}
      </Card>

      <Card title={t("admin.settings.notificationEmails")}>
        <p className="mb-3 max-w-md text-sm text-content/75">{t("admin.settings.notificationEmailsDesc")}</p>
        <form onSubmit={saveEmails} className="flex flex-col gap-3">
          <textarea
            rows={4}
            placeholder="owner@example.com"
            value={emailsText}
            onChange={(e) => setEmailsText(e.target.value)}
            className={`${inputClass} w-full max-w-sm`}
          />
          <button type="submit" className={`${buttonClass} self-start`}>
            {t("admin.settings.saveNotificationEmails")}
          </button>
          {emailsMessage && <p className="text-sm text-content/75">{emailsMessage}</p>}
        </form>
      </Card>

      <Card title={t("admin.settings.manageTimeSlots")}>
        <div className="mb-3 flex items-center gap-2 text-sm text-content/70">
          <span>{t("admin.field.date")}</span>
          <DatePickerField value={blockDate} minDate={todayIso()} onChange={setBlockDate} />
        </div>
        <p className="mb-3 max-w-md text-sm text-content/75">{t("admin.settings.manageTimeSlotsDesc")}</p>
        <div className="mb-4 flex max-w-xl flex-wrap gap-2">
          {slots.map((slot) => {
            let classes = "rounded-lg border px-3 py-2 text-sm transition-colors";
            if (slot.included && slot.blocked) classes += " border-purple-500 bg-purple-700 text-white";
            else if (slot.included) classes += " border-blue-500 bg-blue-700 text-white";
            else if (slot.blocked) classes += " border-red-700 bg-red-800 text-white line-through";
            else classes += " border-line bg-canvas text-content hover:border-accent";
            return (
              <button
                key={slot.time}
                type="button"
                title={slot.included ? t("admin.settings.slotExtraTitle") : t("admin.settings.slotBlockTitle")}
                onClick={() => handleSlotClick(slot)}
                className={classes}
              >
                {slot.time.slice(0, 5)}
              </button>
            );
          })}
        </div>
        <form onSubmit={addExtraTime} className="flex items-center gap-3">
          <TimePickerField required value={newTime} onChange={setNewTime} />
          <button type="submit" className={buttonOutlineClass}>
            {t("admin.settings.addExtraTime")}
          </button>
        </form>
        {slotsError && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            {slotsError}
          </p>
        )}
      </Card>
    </div>
  );
}
