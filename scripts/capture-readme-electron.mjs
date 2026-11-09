/** Capture the real renderer in a private, read-only context, using its default dark theme. */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { DEFAULT_ELECTRON_DEV_PORT } from "../config/app-config.mjs";
import { presentReadmeMedia } from "./readme-media/present.mjs";
import { captureAccess } from "./readme-media/access.mjs";

const root = resolve(fileURLToPath(new URL("..", import.meta.url)));
const base = process.env.VOICESTUDIO_CAPTURE_URL || `http://localhost:${DEFAULT_ELECTRON_DEV_PORT}`;
const appOrigin = new URL(base).origin;
const output = resolve(process.env.VOICESTUDIO_CAPTURE_OUTPUT || join(root, "docs/media/electron"));
const demoReady = process.env.VOICESTUDIO_CAPTURE_DEMO_READY === "1";
const scratch = await mkdtemp(join(tmpdir(), "voicestudio-presentation-"));
const appViewport = { width: 1360, height: 850 };
const demoModel = "k2-fsa/OmniVoice";
const captures = [
  { name: "voice-cloning", title: "Voice cloning", route: "/clone" },
  { name: "voice-design", title: "Voice design", route: "/design" },
  { name: "dubbing", title: "Video dubbing", route: "/dub" },
  { name: "models", title: "Local models", route: "/settings/models/tts" },
  { name: "overview", title: "Workspace overview", route: "/" },
  { name: "model-packs", title: "Model packs", route: "/settings/models" },
];
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  args: ["--mute-audio"],
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
});
const context = await browser.newContext({
  viewport: appViewport,
  deviceScaleFactor: 2,
  colorScheme: "dark",
  locale: "en-US",
});
const blockedMutations = [];
const errors = [];
const shownProfiles = new Set();

try {
  await context.addInitScript(() => {
    localStorage.setItem("voicestudio.setup.complete.v1", "1");
    localStorage.setItem("voicestudio.locale", "en");
    localStorage.setItem(
      "voicestudio.theme.v2",
      JSON.stringify({ mode: "dark", light: "default", dark: "default" }),
    );
    localStorage.setItem(
      "voicestudio.clone.settings.v1",
      JSON.stringify({
        selectedProfileId: "demo0001",
        text: "Every voice has a story.\n\nBring yours to life with VoiceStudio. Clone a familiar tone, design a new character, or carry your words across languages.\n\nCreated on your machine. Ready for your next story.",
      }),
    );
    localStorage.setItem("omnivoice.demoClonePrompted", "1");
  });
  // No personal records or user mutations. Presentation readiness is an explicit
  // browser-only fixture: no model is downloaded, loaded or installed on the host.
  await context.route("**/*", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const access = captureAccess(url.href, request.method(), appOrigin);
    if (access === "deny") {
      if (url.origin === appOrigin) blockedMutations.push(`${request.method()} ${url.pathname}`);
      return route.abort();
    }
    if (
      url.origin !== appOrigin ||
      access === "describe" ||
      request.method() === "HEAD" ||
      !url.pathname.startsWith("/api/")
    )
      return route.continue();
    const path = url.pathname.replace(/^\/api/, "");
    if (path.endsWith("/settings/analytics")) {
      return route.fulfill({ json: { available: false, prompted: true, opted_in: false } });
    }
    if (["/history", "/projects", "/transcriptions", "/export/history"].includes(path)) {
      return route.fulfill({ json: [] });
    }
    if (path === "/profiles") {
      const response = await route.fetch();
      assert(response.ok(), "Cannot read the bundled demo profile");
      const profiles = (await response.json()).filter((profile) => profile.id === "demo0001");
      profiles.forEach((profile) => shownProfiles.add(profile.id));
      return route.fulfill({ response, json: profiles });
    }
    if (demoReady && path === "/setup/status") {
      return route.fulfill({ json: { models_ready: true, missing: [] } });
    }
    if (demoReady && ["/engines", "/models", "/setup/recommendations"].includes(path)) {
      const response = await route.fetch();
      assert(response.ok(), `Cannot read ${path}`);
      const data = await response.json();
      if (path === "/engines") {
        data.tts.active = "omnivoice";
        data.tts.active_model = demoModel;
        data.tts.backends = data.tts.backends.map((engine) =>
          engine.id === "omnivoice" ? { ...engine, available: true } : engine,
        );
      } else {
        data.models = data.models.map((model) =>
          model.repo_id === demoModel ? { ...model, installed: true, incomplete: false } : model,
        );
        if (path === "/setup/recommendations") {
          data.download_gb_remaining = data.models
            .filter((model) => !model.installed)
            .reduce((sum, model) => sum + (model.size_gb || 0), 0);
          data.all_installed = data.models.every((model) => model.installed);
        }
      }
      return route.fulfill({ response, json: data });
    }
    return route.continue();
  });

  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  for (const capture of captures) {
    await page.goto(`${base}/#${capture.route}`, { waitUntil: "domcontentloaded" });
    if (capture.route === "/clone") {
      await page.getByRole("heading", { name: "Voice cloning", exact: true }).waitFor();
      await page
        .getByRole("button", { name: demoReady ? /Synthesize audio/ : "Hear demo" })
        .waitFor();
      await page.getByRole("button", { name: "Voice sample", exact: true }).click();
      await page.locator("canvas").first().waitFor();
    } else if (capture.route === "/design") {
      await page.getByRole("heading", { name: "The Librarian", exact: true }).waitFor();
      const preset = page
        .locator("article")
        .filter({ has: page.getByRole("heading", { name: "The Librarian", exact: true }) });
      await preset.getByRole("button", { name: /Use this design/ }).click();
      await page.getByRole("button", { name: "Narrator", exact: true }).click();
      const mapped = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname.endsWith("/design/describe") &&
          response.request().method() === "POST",
      );
      await page
        .locator("#voice-description")
        .fill("A middle-aged British male narrator with a low pitch.");
      assert((await mapped).ok(), "Voice description must map successfully");
      await page
        .locator("#design-script")
        .fill(
          "Beyond the city lights, a quieter world begins. Every trail holds a story, and every journey starts with a little curiosity.",
        );
      await page.locator("summary").filter({ hasText: "Hear demo" }).click();
    } else if (capture.route === "/dub") {
      await page.getByRole("heading", { name: "See dubbing in action", exact: true }).waitFor();
      const play = page.getByRole("button", { name: "Play", exact: true });
      await play.first().click();
      await page.waitForTimeout(1100);
      await page.getByRole("button", { name: "Pause", exact: true }).first().click();
      await page.getByRole("button", { name: "Play", exact: true }).last().click();
      await page.waitForTimeout(1100);
      await page.getByRole("button", { name: "Pause", exact: true }).first().click();
    } else if (capture.name === "models") {
      await page.locator('[id="engine-omnivoice"]').waitFor();
    } else if (capture.name === "overview") {
      await page.getByRole("heading", { name: "Home", exact: true }).waitFor();
    } else {
      await page.getByRole("heading", { name: "Model packs", exact: true }).waitFor();
    }
    await page.evaluate(async () => {
      await document.fonts.ready;
      await Promise.all([...document.images].map((image) => image.decode().catch(() => {})));
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      document.querySelectorAll("*").forEach((element) => {
        if (element.scrollTop) element.scrollTop = 0;
      });
    });
    await page.mouse.move(1, 1);
    await page.waitForTimeout(350);
    const theme = await page.evaluate(() => ({
      dark: document.documentElement.classList.contains("dark"),
      customPalette: document.documentElement.dataset.themeId,
    }));
    assert(theme.dark && !theme.customPalette, "Capture must use the default dark palette");
    assert(
      await page.evaluate(() =>
        [...document.fonts].some(
          (font) => font.family.includes("Inter") && font.status === "loaded",
        ),
      ),
      "Load the app font before capturing",
    );
    capture.path = join(scratch, `${capture.name}.png`);
    await page.screenshot({ path: capture.path, animations: "disabled" });
    console.log(
      `Captured ${capture.name}: default dark, ${appViewport.width * 2} × ${appViewport.height * 2}`,
    );
  }
  assert.deepEqual([...shownProfiles], ["demo0001"], "Only the bundled demo voice may appear");
  assert.deepEqual(errors, [], "Fix renderer errors before publishing the captures");
  await context.close();
  const sizes = await presentReadmeMedia(browser, captures, output, scratch);
  const { version } = JSON.parse(await readFile(join(root, "package.json"), "utf8"));
  const manifest = {
    capturedAt: new Date().toISOString(),
    sourceCommit: execFileSync("git", ["rev-parse", "HEAD"], {
      cwd: root,
      encoding: "utf8",
    }).trim(),
    version,
    theme: { mode: "dark", palette: "default" },
    appViewport,
    screenshotSize: { width: 2880, height: 2000 },
    previewSize: { width: 1440, height: 1000, format: "lossless WebP" },
    deviceScaleFactor: 2,
    state: demoReady
      ? "browser-only demo readiness fixture; bundled samples; no live generation"
      : "live read-only backend; bundled samples",
    profiles: [...shownProfiles],
    blockedMutations,
    files: sizes,
  };
  await writeFile(join(output, "capture-manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  console.log(JSON.stringify(manifest, null, 2));
  if (process.env.VOICESTUDIO_CAPTURE_KEEP_RAW === "1") console.log(`Raw captures: ${scratch}`);
  else await rm(scratch, { recursive: true, force: true });
} finally {
  await context.close();
  await browser.close();
}
