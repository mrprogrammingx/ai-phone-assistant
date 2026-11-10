import { useEffect, useState } from "react";
import { getCustomers } from "../../lib/adminApi";
import { Table, buttonOutlineClass } from "../../components/admin/ui";
import DatePickerField from "../../components/admin/DatePickerField";
import { useLanguage } from "../../lib/i18n/LanguageContext";

export default function AdminCustomers() {
  const { t } = useLanguage();
  const [date, setDate] = useState("");
  const [customers, setCustomers] = useState([]);

  useEffect(() => {
    getCustomers(date || undefined).then(setCustomers);
  }, [date]);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="font-display text-2xl">{t("admin.customers.title")}</h1>

      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2 text-sm text-content/70">
          <span>{t("admin.field.date")}</span>
          <DatePickerField value={date} minDate="2000-01-01" onChange={setDate} />
        </div>
        <button type="button" onClick={() => setDate("")} className={buttonOutlineClass}>
          {t("admin.customers.showAll")}
        </button>
      </div>
      <p className="text-sm text-content/70">
        {date ? t("admin.customers.showingDate", { date }) : t("admin.customers.showingAll")}
      </p>

      {customers.length === 0 ? (
        <p className="text-content/70">{t("admin.customers.noneFound")}</p>
      ) : (
        <Table columns={[t("admin.field.name"), t("admin.field.phone"), t("admin.table.visits"), t("admin.table.signedUp")]}>
          {customers.map((customer) => (
            <tr key={customer.id} className="border-b border-line">
              <td className="px-3 py-2">{customer.name}</td>
              <td className="px-3 py-2">{customer.phone}</td>
              <td className="px-3 py-2">{customer.visit_count}</td>
              <td className="px-3 py-2">{new Date(customer.created_at).toLocaleDateString()}</td>
            </tr>
          ))}
        </Table>
      )}
    </div>
  );
}
