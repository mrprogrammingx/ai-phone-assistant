import { useCallback, useEffect, useState } from "react";
import { getHistoryDates, getHistoryForDate, markDone, markNoShow } from "../../lib/adminApi";
import { EntryCard, EntryRow, StatusPill, StatusSelect, Table } from "../../components/admin/ui";
import DatePickerField from "../../components/admin/DatePickerField";
import { useLanguage } from "../../lib/i18n/LanguageContext";

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function AdminHistory() {
  const { t } = useLanguage();
  const [dates, setDates] = useState([]);
  const [date, setDate] = useState(todayIso());
  const [entries, setEntries] = useState([]);
  const isToday = date === todayIso();

  useEffect(() => {
    getHistoryDates().then(setDates);
  }, []);

  const loadHistory = useCallback(() => {
    if (date) getHistoryForDate(date).then(setEntries);
  }, [date]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  async function handleStatusChange(id, newStatus) {
    if (newStatus === "done") await markDone(id);
    else if (newStatus === "no_show") await markNoShow(id);
    loadHistory();
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="font-display text-2xl">{t("admin.history.title")}</h1>

      <div className="flex items-center gap-2 text-sm text-content/70">
        <span>{t("admin.field.date")}</span>
        <DatePickerField value={date} minDate="2000-01-01" onChange={setDate} />
      </div>
      <p className="text-sm text-content/70">
        {dates.length > 0 ? t("admin.history.datesWithHistory", { dates: dates.join(", ") }) : t("admin.history.noHistoryYet")}
      </p>

      {entries.length === 0 ? (
        <p className="text-content/70">{t("admin.dashboard.noEntries")}</p>
      ) : (
        <>
          <div className="hidden md:block">
            <Table
              columns={[
                t("admin.table.number"),
                t("admin.table.time"),
                t("admin.field.name"),
                t("admin.field.phone"),
                t("admin.field.note"),
                t("admin.field.status"),
                t("admin.field.checkedIn"),
              ]}
            >
              {entries.map((entry) => (
                <tr key={entry.id} className="border-b border-line">
                  <td className="px-3 py-2">{entry.position}</td>
                  <td className="px-3 py-2">{entry.appointment_time.slice(0, 5)}</td>
                  <td className="px-3 py-2">{entry.customer.name}</td>
                  <td className="px-3 py-2">{entry.customer.phone}</td>
                  <td className="px-3 py-2">{entry.note || ""}</td>
                  <td className="px-3 py-2">
                    {isToday ? (
                      <StatusSelect status={entry.status} onChange={(next) => handleStatusChange(entry.id, next)} />
                    ) : (
                      <StatusPill status={entry.status} />
                    )}
                  </td>
                  <td className="px-3 py-2">{new Date(entry.created_at).toLocaleTimeString()}</td>
                </tr>
              ))}
            </Table>
          </div>

          <div className="flex flex-col gap-4 md:hidden">
            {entries.map((entry) => (
              <EntryCard key={entry.id} title={`#${entry.position} · ${entry.appointment_time.slice(0, 5)}`}>
                <EntryRow label={t("admin.field.name")}>{entry.customer.name}</EntryRow>
                <EntryRow label={t("admin.field.phone")}>{entry.customer.phone}</EntryRow>
                {entry.note && <EntryRow label={t("admin.field.note")}>{entry.note}</EntryRow>}
                <EntryRow label={t("admin.field.status")}>
                  {isToday ? (
                    <StatusSelect status={entry.status} onChange={(next) => handleStatusChange(entry.id, next)} />
                  ) : (
                    <StatusPill status={entry.status} />
                  )}
                </EntryRow>
                <EntryRow label={t("admin.field.checkedIn")}>{new Date(entry.created_at).toLocaleTimeString()}</EntryRow>
              </EntryCard>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
