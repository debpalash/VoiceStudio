import { chromium } from "playwright";
import assert from "node:assert/strict";
const browser = await chromium.launch({
  ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH
    ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH }
    : { channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge' }),
  headless: true,
});
const page = await browser.newPage();
const baseUrl = process.env.VOICESTUDIO_UI_URL || "http://localhost:3912";
await page.addInitScript(() => {
  localStorage.setItem("voicestudio.setup.complete.v1", "1");
});
try {
  for (const width of [1920, 1440, 960]) {
    await page.setViewportSize({ width, height: 900 });
    for (const route of [
      "stories",
      "audiobook",
      "tools",
      "batch",
      "gallery",
      "projects",
      "dub",
      "design",
      "transcriptions",
    ]) {
      await page.goto(baseUrl + "/#/" + route);
      await page.waitForTimeout(100);
      const sideCandidates = page.locator("[data-slot=secondary-sidebar]");
      await sideCandidates.first().waitFor({ state: "attached" });
      await sideCandidates.first().getByRole("separator").waitFor({ state: "visible" });
      const compactMain = page.locator("[data-slot=compact-main-sidebar]");
      // A section with its own pane keeps navigation to the icon rail at every width.
      await compactMain.waitFor();
      assert.equal(Math.round((await compactMain.boundingBox()).width), 48);
      // The notifications bell lives in the top-right account corner on every page and
      // platform, not in the sidebar.
      assert.equal(await compactMain.locator("button .lucide-bell").count(), 0);
      assert.equal(
        await page.locator('[data-slot="account-corner"] button .lucide-bell').count(),
        1,
      );
      // Voice, Stories and Dubbing are flyout group buttons; the other five are direct links.
      const compactNav = compactMain.getByRole("navigation", {
        name: "Workspaces",
      });
      assert.equal(await compactNav.getByRole("button").count(), 3);
      assert.equal(await compactNav.getByRole("link").count(), 5);
      assert.equal(
        await page.locator(".brand-sidebar:not([data-slot=compact-main-sidebar])")
          .count(),
        0,
      );
      const openMain = compactMain.getByRole("button", {
        name: "Toggle sidebar",
      });
      await openMain.click();
      await compactMain.waitFor({ state: "detached" });
      const expandedMain = page.locator(".brand-sidebar:not([data-slot=compact-main-sidebar])");
      await expandedMain.waitFor();
      const before = await expandedMain.boundingBox();
      const separator = expandedMain.getByRole("separator");
      await separator.focus();
      const currentWidth = Number(
        await separator.getAttribute("aria-valuenow"),
      );
      const minimumWidth = Number(
        await separator.getAttribute("aria-valuemin"),
      );
      const maximumWidth = Number(
        await separator.getAttribute("aria-valuemax"),
      );
      const grow = currentWidth <= minimumWidth;
      await separator.press(grow ? "ArrowRight" : "ArrowLeft");
      const after = await expandedMain.boundingBox();
      const resizeDelta = Math.round(Math.abs(before.width - after.width));
      assert.ok(
        resizeDelta > 0 && resizeDelta <= 20,
        `${route} main sidebar did not resize: ${before.width}px -> ${after.width}px (now ${currentWidth}, min ${minimumWidth}, max ${maximumWidth})`,
      );
      await expandedMain.getByRole("button", { name: "Close" }).click();
      await compactMain.waitFor();
      await page.waitForFunction(() => {
        const elements = document.querySelectorAll(
          '[data-slot="secondary-sidebar"]',
        );
        return (
          elements.length === 1 &&
          elements[0].getBoundingClientRect().width >= 192
        );
      });
      const side = sideCandidates.first();
      // Let the shell toggle and ResizeObserver settle before recording the width.
      await page.waitForTimeout(200);
      const expanded = await side.evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return { width: bounds.width, height: bounds.height };
      });
      assert.ok(
        expanded.width >= 192 && expanded.width <= 512,
        `${route} secondary sidebar width was ${expanded.width}px at ${width}px viewport`,
      );
      if (route === "dub") {
        const transcript = await page
          .locator("[data-slot=dub-transcript]")
          .boundingBox();
        assert.ok(
          transcript && transcript.width >= 480,
          `Dubbing transcript was squeezed to ${transcript?.width ?? 0}px at ${width}px viewport`,
        );
      }
      await side.getByRole("button", { name: "Collapse pane", exact: true }).click();
      assert.ok(
        await side.evaluate(
          (element) => element.getBoundingClientRect().width < 50,
        ),
      );
      // Collapsed, the pane has one expand control and an empty titlebar row.
      assert.equal(await side.locator("[data-slot=secondary-sidebar-header] button").count(), 0);
      await side.locator("[data-slot=secondary-sidebar-expand]").click();
      await page.waitForFunction((expected) =>
        document.querySelector("[data-slot=secondary-sidebar]")?.getBoundingClientRect().width === expected,
        expanded.width,
      );
      assert.ok(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
    }
  }
  await page.setViewportSize({ width: 960, height: 900 });
  await page.goto(baseUrl + "/#/audiobook");
  await page
    .locator("[data-slot=compact-main-sidebar]")
    .getByRole("button", { name: "Voice", exact: true })
    .click();
  await page.getByRole("link", { name: "Clone", exact: true }).click();
  await page.waitForURL("**/#/clone");
  // Clone shows its library as a context pane, so navigation stays an icon rail.
  assert.equal(
    await page.locator("[data-slot=compact-main-sidebar]").count(),
    1,
  );
  await page.getByRole("tab", { name: "Saved voices", exact: true }).waitFor();
  await page.setViewportSize({ width: 640, height: 900 });
  for (const route of [
    "stories",
    "audiobook",
    "tools",
    "batch",
    "gallery",
    "projects",
    "dub",
    "design",
    "transcriptions",
  ]) {
    await page.goto(baseUrl + "/#/" + route);
    await page.locator("[data-slot=compact-main-sidebar]").waitFor();
    const side = page.locator("[data-slot=secondary-sidebar]").first();
    const narrowSide = await side.boundingBox();
    const primary = side.locator("xpath=following-sibling::*[1]");
    const narrowPrimary = await primary.boundingBox();
    assert.ok(
      narrowSide && narrowSide.width >= 580 && narrowSide.height <= 360,
      `${route} controls did not stack above its workspace`,
    );
    // Routes with their own tab bar (Gallery) push the stack down by that bar's height.
    const extraChrome = narrowSide ? Math.max(0, narrowSide.y - 52) : 0;
    assert.ok(
      narrowPrimary && narrowPrimary.width >= 580 && narrowPrimary.height >= 480 - extraChrome,
      `${route} primary workspace disappeared at compact width`,
    );
    assert.ok(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      `${route} overflowed horizontally at compact width`,
    );
  }
  for (const width of [1920, 960, 640]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(baseUrl + "/#/settings/appearance");
    const row = page.locator("[data-slot=settings-row]").first();
    await row.waitFor();
    const bounds = await row.boundingBox();
    assert.ok(
      // Settings now sits beside both the main sidebar and its own nav; the content must
      // still reach the right edge of the window (minus the page gutter).
      bounds.x + bounds.width >= width - 48,
      "Settings content should fill available width",
    );
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    if (width === 960) {
      const labels = page.locator('[data-slot=settings-nav-label]');
      const states = await labels.evaluateAll((elements) =>
        elements.map((element) => {
          const style = getComputedStyle(element);
          return {
            clipped: element.scrollWidth > element.clientWidth,
            overflow: style.overflow,
            textOverflow: style.textOverflow,
          };
        }),
      );
      assert.ok(states.some((state) => state.clipped), 'Expected a narrow Settings label');
      assert.ok(
        states.filter((state) => state.clipped).every(
          (state) => state.overflow === 'hidden' && state.textOverflow === 'ellipsis',
        ),
        'Narrow Settings labels must end with an ellipsis instead of hard clipping',
      );
    }
  }
  console.log(
    "Responsive settings width, compact main rail, and secondary sidebar checks passed.",
  );
} finally {
  await browser.close();
}
