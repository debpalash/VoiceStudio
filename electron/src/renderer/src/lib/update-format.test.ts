import { describe, expect, test } from "vitest";
import {
  formatBytes,
  progressDue,
  updateErrorKey,
  updateErrorTitleKey,
  updateReleaseUrl,
} from "./update-format";

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

describe("updateErrorTitleKey", () => {
  test("names the failed step", () => {
    expect(updateErrorTitleKey({ errorKind: "check", error: "boom" })).toBe("update.error_check");
    expect(updateErrorTitleKey({ errorKind: "check", error: "HTTP 503" })).toBe(
      "update.error_network",
    );
    expect(updateErrorTitleKey({ errorKind: "download" })).toBe("update.error_download");
    expect(updateErrorTitleKey({ errorKind: "verify" })).toBe("update.error_verify");
    expect(updateErrorTitleKey({ errorKind: "install" })).toBe("update.error_install");
    expect(updateErrorTitleKey({ errorKind: "not-applied" })).toBe("update.error_not_applied");
    expect(updateErrorTitleKey({ error: "HTTP 502" })).toBe("update.error_network");
  });
});

describe("updateReleaseUrl", () => {
  test("links a release version, or the releases list otherwise", () => {
    expect(updateReleaseUrl("0.5.8-beta.3")).toBe(
      "https://github.com/debpalash/VoiceStudio/releases/tag/v0.5.8-beta.3",
    );
    expect(updateReleaseUrl("v0.5.9")).toBe(
      "https://github.com/debpalash/VoiceStudio/releases/tag/v0.5.9",
    );
    expect(updateReleaseUrl(undefined)).toBe("https://github.com/debpalash/VoiceStudio/releases");
    expect(updateReleaseUrl("../../evil")).toBe(
      "https://github.com/debpalash/VoiceStudio/releases",
    );
  });
});

describe("progressDue", () => {
  const shown = { status: "downloading" as const, pct: 10.2, at: 1_000 };
  test("refreshes about once a second or per whole percent", () => {
    expect(progressDue(null, { status: "downloading", pct: 0 }, 0)).toBe(true);
    expect(progressDue(shown, { status: "downloading", pct: 10.9 }, 1_400)).toBe(false);
    expect(progressDue(shown, { status: "downloading", pct: 11.2 }, 1_400)).toBe(true);
    expect(progressDue(shown, { status: "downloading", pct: 10.5 }, 2_000)).toBe(true);
    expect(progressDue(shown, { status: "downloaded", pct: 10.5 }, 1_100)).toBe(true);
  });
});
