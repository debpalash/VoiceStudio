import assert from "node:assert/strict";
import test from "node:test";
import { captureAccess } from "../../scripts/readme-media/access.mjs";

const origin = "http://localhost:4351";

test("capture can read the local renderer, API and media metadata", () => {
  for (const path of ["/", "/src/main.tsx", "/api/profiles", "/api/demo_audio/demo_voice.wav"]) {
    assert.equal(captureAccess(origin + path, "GET", origin), "read");
    assert.equal(captureAccess(origin + path, "HEAD", origin), "read");
  }
});

test("capture cannot mutate through either API or non-API paths", () => {
  for (const method of ["POST", "PUT", "PATCH", "DELETE"]) {
    for (const path of [
      "/api/generate",
      "/api/models/download",
      "/api/profiles/demo0001",
      "/mcp",
      "/profiles",
    ]) {
      assert.equal(captureAccess(origin + path, method, origin), "deny");
    }
  }
});

test("the stateless voice-description parser is the sole permitted POST", () => {
  assert.equal(captureAccess(origin + "/api/design/describe", "POST", origin), "describe");
  assert.equal(captureAccess(origin + "/api/design/describe", "DELETE", origin), "deny");
  assert.equal(captureAccess("https://example.com/api/design/describe", "POST", origin), "deny");
});

test("remote access is limited to the existing public GitHub star lookup", () => {
  const stars = "https://api.github.com/repos/debpalash/VoiceStudio";
  assert.equal(captureAccess(stars, "GET", origin), "read");
  for (const url of [
    stars + "/issues",
    stars + "?content=private",
    "https://api.github.com.example.com/repos/debpalash/VoiceStudio",
    "https://example.com",
  ]) {
    assert.equal(captureAccess(url, "GET", origin), "deny");
  }
  assert.equal(captureAccess(stars, "POST", origin), "deny");
});
