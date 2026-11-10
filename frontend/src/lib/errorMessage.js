// Turns a FastAPI error `detail` into one sentence fit to show a person. Validation
// errors arrive as a list of objects; anything unrecognised gets the plain fallback,
// so raw JSON or "[object Object]" can never reach the screen.
export function errorMessage(detail, status) {
  const fallback = `Request failed (${status})`;

  if (typeof detail === "string") return detail || fallback;

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => item?.msg)
      .filter((message) => typeof message === "string" && message)
      .map((message) => message.replace(/^Value error, /, ""));
    if (messages.length > 0) return messages.join(". ");
  }

  return fallback;
}

// Validation errors keyed by the form field they belong to, so each can be shown
// beside its input. Errors without a field are left to errorMessage.
export function fieldErrors(detail) {
  const fields = {};
  if (!Array.isArray(detail)) return fields;
  for (const item of detail) {
    const field = Array.isArray(item?.loc) ? item.loc[item.loc.length - 1] : null;
    if (typeof field !== "string" || typeof item?.msg !== "string" || !item.msg) continue;
    if (!(field in fields)) fields[field] = item.msg.replace(/^Value error, /, "");
  }
  return fields;
}
