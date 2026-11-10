// Date and time labels shown on the site.

export function formatTimeLabel(timeStr) {
  const [h, m] = timeStr.split(":").map(Number);
  const hour12 = h % 12 === 0 ? 12 : h % 12;
  const period = h >= 12 ? "PM" : "AM";
  return `${hour12}:${String(m).padStart(2, "0")} ${period}`;
}

export function formatDateLabelLong(dateStr) {
  const [y, m, d] = dateStr.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  return date.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" });
}

export function formatMonthYear(date) {
  return new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric" }).format(date);
}

// Sunday-first, matching the calendar grid's column order.
export function weekdayNarrowLabels() {
  return ["S", "M", "T", "W", "T", "F", "S"];
}

export function formatFullDate(date) {
  return new Intl.DateTimeFormat("en-US", { weekday: "short", day: "numeric", month: "short", year: "numeric" }).format(
    date
  );
}
