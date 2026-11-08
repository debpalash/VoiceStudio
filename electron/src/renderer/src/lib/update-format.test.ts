import { describe, expect, test } from "vitest";
import { formatBytes, updateErrorKey } from "./update-format";

describe("updateErrorKey", () => {
  test.each([
    "Error: HTTP 502 Bad Gateway",
    "Cannot download: 503",
    "<!DOCTYPE html><html><body>Unicorn</body></html>",
    "connect ETIMEDOUT 140.82.112.4:443",
    "getaddrinfo ENOTFOUND github.com",
  ])("maps %s to the friendly connection message", (raw) => {
    expect(updateErrorKey(raw)).toBe("update.error_network");
  });
  test("falls back to the generic message for other or missing errors", () => {
    expect(updateErrorKey("sha512 checksum mismatch")).toBe(
      "update.error_generic",
    );
    expect(updateErrorKey(undefined)).toBe("update.error_generic");
  });
  test("formats bytes", () => {
    expect(formatBytes(2048)).toBe("2 KB");
  });
});
