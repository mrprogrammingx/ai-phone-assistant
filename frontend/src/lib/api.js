// Talks to the existing FastAPI booking backend (app/routers/queue.py).
// In dev, Vite proxies /queue/* to http://localhost:8002 (see vite.config.js).
// In production, set VITE_API_BASE_URL to the deployed API origin.
import { errorMessage, fieldErrors } from "./errorMessage";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!res.ok) {
    let detail;
    try {
      const body = await res.json();
      detail = body?.detail;
    } catch {
      // ignore non-JSON error bodies
    }
    const error = new Error(errorMessage(detail, res.status));
    error.fields = fieldErrors(detail);
    throw error;
  }

  if (res.status === 204) return null;
  return res.json();
}

export function getShopStatus() {
  return request("/admin/shop-status");
}

export function getAvailableTimes(forDate) {
  const params = new URLSearchParams({ for_date: forDate });
  return request(`/queue/available-times?${params}`);
}

export function checkIn({ name, phone, appointmentDate, appointmentTime, note }) {
  return request("/queue/checkin", {
    method: "POST",
    body: JSON.stringify({
      name,
      phone,
      appointment_date: appointmentDate,
      appointment_time: appointmentTime,
      note: note || null,
    }),
  });
}

export function getMyBookings(phone, forDate) {
  const params = new URLSearchParams({ phone });
  if (forDate) params.set("for_date", forDate);
  return request(`/queue/my-bookings?${params}`);
}
