/** Read-only capture access, plus the app's local, stateless description parser. */
export function captureAccess(requestUrl, method, appOrigin) {
  const url = new URL(requestUrl);
  if (
    method === "GET" &&
    url.origin === "https://api.github.com" &&
    url.pathname === "/repos/debpalash/VoiceStudio" &&
    !url.search
  )
    return "read";
  if (url.origin !== appOrigin) return "deny";
  if (method === "GET" || method === "HEAD") return "read";
  if (method === "POST" && url.pathname === "/api/design/describe") return "describe";
  return "deny";
}
