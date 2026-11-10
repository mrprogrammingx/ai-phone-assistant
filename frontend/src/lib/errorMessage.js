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
