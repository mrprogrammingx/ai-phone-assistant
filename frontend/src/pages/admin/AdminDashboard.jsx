import { useCallback, useEffect, useRef, useState } from "react";
import { getQueue, markDone, markNoShow, setNote } from "../../lib/adminApi";
import { EntryCard, EntryRow, StatusSelect, Table, inputClass } from "../../components/admin/ui";
import DatePickerField from "../../components/admin/DatePickerField";
import { useLanguage } from "../../lib/i18n/LanguageContext";

function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export default function AdminDashboard() {
  const { t } = useLanguage();
  const [date, setDate] = useState(todayIso());
  const [entries, setEntries] = useState([]);
  const [notes, setNotes] = useState({});
  const noteInputFocused = useRef(false);

  const loadQueue = useCallback(async () => {
    if (noteInputFocused.current) return;
    const data = await getQueue(date);
    setEntries(data);
    setNotes((prev) => {
      const next = { ...prev };
      for (const entry of data) {
        if (!(entry.id in next)) next[entry.id] = entry.note || "";
      }
      return next;
    });
  }, [date]);

  useEffect(() => {
    loadQueue();
    const interval = setInterval(loadQueue, 5000);
    return () => clearInterval(interval);
  }, [loadQueue]);

  async function handleStatusChange(id, newStatus) {
    if (newStatus === "done") await markDone(id);
    else if (newStatus === "no_show") await markNoShow(id);
    loadQueue();
  }

  async function handleNoteBlur(id) {
    noteInputFocused.current = false;
    await setNote(id, notes[id]);
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="font-display text-2xl">{t("admin.dashboard.title")}</h1>

      <div className="flex items-center gap-2 text-sm text-content/70">
        <span>{t("admin.field.date")}</span>
        <DatePickerField value={date} minDate="2000-01-01" onChange={setDate} />
      </div>

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
              ]}
            >
              {entries.map((entry) => (
                <tr key={entry.id} className="border-b border-line">
                  <td className="px-3 py-2">{entry.position}</td>
                  <td className="px-3 py-2">{entry.appointment_time.slice(0, 5)}</td>
                  <td className="px-3 py-2">{entry.customer.name}</td>
                  <td className="px-3 py-2">{entry.customer.phone}</td>
                  <td className="px-3 py-2">
                    <input
                      type="text"
                      placeholder={t("admin.field.note")}
                      value={notes[entry.id] ?? ""}
                      onFocus={() => {
                        noteInputFocused.current = true;
                      }}
                      onChange={(e) => setNotes((prev) => ({ ...prev, [entry.id]: e.target.value }))}
                      onBlur={() => handleNoteBlur(entry.id)}
                      className={`${inputClass} w-full`}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <StatusSelect status={entry.status} onChange={(next) => handleStatusChange(entry.id, next)} />
                  </td>
                </tr>
              ))}
            </Table>
          </div>

          <div className="flex flex-col gap-4 md:hidden">
            {entries.map((entry) => (
              <EntryCard
                key={entry.id}
                title={`#${entry.position} · ${entry.appointment_time.slice(0, 5)}`}
                footer={
                  <div className="border-t border-line px-4 py-3">
                    <input
                      type="text"
                      placeholder={t("admin.dashboard.addNote")}
                      value={notes[entry.id] ?? ""}
                      onFocus={() => {
                        noteInputFocused.current = true;
                      }}
                      onChange={(e) => setNotes((prev) => ({ ...prev, [entry.id]: e.target.value }))}
                      onBlur={() => handleNoteBlur(entry.id)}
                      className={`${inputClass} w-full`}
                    />
                  </div>
                }
              >
                <EntryRow label={t("admin.field.name")}>{entry.customer.name}</EntryRow>
                <EntryRow label={t("admin.field.phone")}>{entry.customer.phone}</EntryRow>
                <EntryRow label={t("admin.field.status")}>
                  <StatusSelect status={entry.status} onChange={(next) => handleStatusChange(entry.id, next)} />
                </EntryRow>
              </EntryCard>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
