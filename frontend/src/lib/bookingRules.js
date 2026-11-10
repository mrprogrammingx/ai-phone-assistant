// The booking form's limits. They mirror the backend, which still decides:
// app/schemas.py (name, note), app/phone.py (phone) and app/services/booking.py
// (horizon). tests/test_checkin_validation.py fails if these numbers drift apart.
export const BOOKING_HORIZON_DAYS = 90;
export const MAX_NAME_LENGTH = 100;
export const MAX_NOTE_LENGTH = 500;

// Counts characters the way the backend does, so an emoji is one character.
function length(text) {
  return [...text].length;
}

// The translation key of what is wrong with a name, or null.
export function nameProblem(value) {
  const name = value.trim();
  if (name === "") return "bookingForm.blankName";
  if (length(name) > MAX_NAME_LENGTH) return "bookingForm.nameTooLong";
  return null;
}

export function noteProblem(value) {
  return length(value.trim()) > MAX_NOTE_LENGTH ? "bookingForm.noteTooLong" : null;
}

// The same steps as normalize_phone in app/phone.py: E.164, national numbers are Swiss.
// Returns null for a number the backend would refuse.
export function normalizePhone(value) {
  let compact = value.trim().replace("(0)", "").replace(/[\s().\-/]/g, "");
  if (compact.startsWith("00")) compact = `+${compact.slice(2)}`;
  else if (compact.startsWith("0")) compact = `+41${compact.slice(1)}`;
  else if (compact.startsWith("41") && compact.length === 11) compact = `+${compact}`;

  if (!/^\+[1-9][0-9]{7,14}$/.test(compact)) return null;
  if (compact.startsWith("+41") && compact.length !== 12) return null;
  return compact;
}

export function phoneProblem(value) {
  return normalizePhone(value) === null ? "bookingForm.invalidPhone" : null;
}

// The last day that can be booked, as YYYY-MM-DD, counted from the browser's today.
export function lastBookableDate(todayIso) {
  const [year, month, day] = todayIso.split("-").map(Number);
  const last = new Date(year, month - 1, day + BOOKING_HORIZON_DAYS);
  const pad = (number) => String(number).padStart(2, "0");
  return `${last.getFullYear()}-${pad(last.getMonth() + 1)}-${pad(last.getDate())}`;
}
