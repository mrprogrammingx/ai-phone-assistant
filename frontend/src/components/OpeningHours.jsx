import { useEffect, useState } from "react";
import { getShopStatus } from "../lib/api";
import { formatTimeLabel } from "../lib/i18n/dateFormat";
import { useLanguage } from "../lib/i18n/LanguageContext";

const WEEK = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

// Hours come from the booking API, so the site cannot disagree with what can
// actually be booked. No hours are shown if the API cannot be reached.
export default function OpeningHours({ className = "", timeClassName = "" }) {
  const { t } = useLanguage();
  const [status, setStatus] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getShopStatus()
      .then((data) => {
        if (!cancelled) setStatus(data);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (failed) return <p className={className}>{t("hours.unavailable")}</p>;
  if (!status) return null;

  const range = `${formatTimeLabel(status.open_time)} – ${formatTimeLabel(status.close_time)}`;

  return (
    <ul className={className}>
      {WEEK.map((code) => {
        const open = status.open_days.includes(code);
        return (
          <li key={code} className="flex justify-between gap-6">
            <span>{t(`day.${code}`)}</span>
            <span className={open ? timeClassName : ""}>{open ? range : t("day.closed")}</span>
          </li>
        );
      })}
    </ul>
  );
}
