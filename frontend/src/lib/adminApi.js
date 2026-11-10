// Talks to the backend's admin-only endpoints. Session auth via cookie, so
// every request needs credentials: "include" (matters when frontend and API
// run on different origins/ports in dev).
const API_BASE = import.meta.env.VITE_API_BASE_URL || "";

export class AuthError extends Error {}

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (res.status === 401) {
    throw new AuthError("Admin login required");
  }

  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) {
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
      }
    } catch {
      // ignore non-JSON error bodies
    }
    throw new Error(detail);
  }

  if (res.status === 204) return null;
  return res.json();
}

export const getMe = () => request("/api/me");
export const login = (username, password) =>
  request("/api/login", { method: "POST", body: JSON.stringify({ username, password }) });
export const logout = () => request("/api/logout", { method: "POST" });

export const getQueue = (forDate) => request(`/queue?for_date=${forDate}`);
export const setNote = (id, note) =>
  request(`/queue/${id}/note`, { method: "POST", body: JSON.stringify({ note: note || null }) });
export const callNext = () => request("/queue/call-next", { method: "POST" });
export const markDone = (id) => request(`/queue/${id}/done`, { method: "POST" });
export const markNoShow = (id) => request(`/queue/${id}/no-show`, { method: "POST" });
export const resetQueue = () => request("/admin/reset-queue", { method: "POST" });

export const getHistoryDates = () => request("/queue/history/dates");
export const getHistoryForDate = (queueDate) => request(`/queue/history/${queueDate}`);

export const getCustomers = (forDate) =>
  request(forDate ? `/queue/customers?for_date=${forDate}` : "/queue/customers");

export const getShopStatus = () => request("/admin/shop-status");
export const setShopStatus = (payload) =>
  request("/admin/shop-status", { method: "POST", body: JSON.stringify(payload) });
export const setOpenDays = (open_days) =>
  request("/admin/open-days", { method: "POST", body: JSON.stringify({ open_days }) });
export const setScheduleSettings = (payload) =>
  request("/admin/schedule-settings", { method: "POST", body: JSON.stringify(payload) });
export const setNotificationEmails = (notification_emails) =>
  request("/admin/notification-emails", {
    method: "POST",
    body: JSON.stringify({ notification_emails }),
  });

export const getAvailableTimesAdmin = (forDate) => request(`/queue/available-times?for_date=${forDate}`);
export const blockSlot = (date, time) =>
  request("/admin/blocked-slots", { method: "POST", body: JSON.stringify({ date, time }) });
export const unblockSlot = (date, time) =>
  request("/admin/blocked-slots", { method: "DELETE", body: JSON.stringify({ date, time }) });
export const addIncludedSlot = (date, time) =>
  request("/admin/included-slots", { method: "POST", body: JSON.stringify({ date, time }) });
export const removeIncludedSlot = (date, time) =>
  request("/admin/included-slots", { method: "DELETE", body: JSON.stringify({ date, time }) });
