// Live smoke for VoiceStudio Cloud sign-in against production. Needs a built app
// (`bun run build`), a display (xvfb-run on Linux) and network access to app.voicestudio.sh.
// It stops at the sign-in page: finishing sign-in needs a real account.
import { _electron as electron } from "playwright";
import { mkdtempSync } from "node:fs";
import { connect } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { ACCOUNT_CONFIG } from "../../config/account-config.mjs";

const { issuer: ISSUER, dashboard: DASHBOARD, clientId: CLIENT_ID } =
  ACCOUNT_CONFIG.environments.prod;
const workspaceRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const executable =
  process.platform === "win32"
    ? join(workspaceRoot, "electron/node_modules/electron/dist/electron.exe")
    : resolve(
        workspaceRoot,
        "electron/node_modules/electron/dist",
        process.platform === "darwin"
          ? "Electron.app/Contents/MacOS/Electron"
          : "electron",
      );
const screenshots = process.env.ACCOUNT_SMOKE_SCREENSHOTS;
const launchEnvironment = { ...process.env };
delete launchEnvironment.ELECTRON_RUN_AS_NODE;

const listening = (port) =>
  new Promise((done) => {
    const socket = connect(
      { host: "127.0.0.1", port },
      () => (socket.destroy(), done(true)),
    );
    socket.on("error", () => done(false));
  });

const app = await electron.launch({
  executablePath: executable,
  args: [
    join(workspaceRoot, "electron/out/main/index.js"),
    "--user-data-dir=" + mkdtempSync(join(tmpdir(), "voicestudio-account-")),
  ],
  env: {
    ...launchEnvironment,
    ELECTRON_RENDERER_URL: "",
    VOICESTUDIO_CLOUD_ENV: "prod",
    VOICESTUDIO_ELECTRON_PROXY_PORT: "0",
    VOICESTUDIO_SKIP_BACKEND: "1",
    VOICESTUDIO_ALLOW_MULTIPLE_INSTANCES: "1",
  },
});

try {
  // Capture the browser hand-off instead of opening a real browser.
  await app.evaluate(({ shell }) => {
    shell.__opened = [];
    shell.openExternal = async (url) => void shell.__opened.push(url);
  });
  const opened = () => app.evaluate(({ shell }) => shell.__opened);

  const page = await app.firstWindow();
  page.setDefaultTimeout(20000);
  await page.waitForFunction(() => Boolean(window.voicestudio?.account));
  const account = (fn, arg) =>
    page.evaluate(([f, a]) => window.voicestudio.account[f](a), [fn, arg]);

  // Off by default: nothing in the corner, sign-in refuses to start.
  let state = await account("get");
  assert.equal(
    state.available,
    true,
    "non-packaged builds carry account features",
  );
  assert.equal(state.accountEnabled, false);
  assert.equal(state.syncEnabled, false);
  assert.equal(state.signedIn, false);
  assert.equal(
    await page.locator('[data-slot="account-corner"] >> text=Sign in').count(),
    0,
  );
  await account("signIn");
  assert.deepEqual(
    await opened(),
    [],
    "no browser hand-off while account features are off",
  );

  // On: the corner shows Sign in with the notifications bell to its right.
  state = await account("setSwitches", { accountEnabled: true });
  assert.equal(state.accountEnabled, true);
  const corner = page.locator('[data-slot="account-corner"]');
  const signIn = corner.getByRole("button", { name: "Sign in" });
  await signIn.waitFor();
  const buttons = corner.locator(":scope > *");
  const first = await buttons.first().boundingBox();
  const last = await buttons.last().boundingBox();
  assert.ok(
    first && last && (await buttons.count()) >= 2,
    "account control and bell share the corner",
  );
  assert.ok(
    first.x + first.width <= last.x,
    "bell sits right of the account control",
  );
  const viewport = await page.evaluate(() => window.innerWidth);
  assert.ok(last.x + last.width <= viewport, "corner stays on screen");
  if (screenshots)
    await page.screenshot({
      path: join(screenshots, "account-signed-out.png"),
    });

  // Sign in: the browser opens the dashboard's app sign-in with PKCE and a loopback redirect.
  await signIn.click();
  await page.waitForFunction(
    async () => (await window.voicestudio.account.get()).signingIn,
  );
  await assert.doesNotReject(async () => {
    for (let i = 0; i < 100 && !(await opened()).length; i++)
      await page.waitForTimeout(100);
  });
  const [authorize] = await opened();
  assert.ok(authorize, "sign-in handed an authorize URL to the browser");
  const url = new URL(authorize);
  assert.equal(
    `${url.origin}${url.pathname}`,
    `${DASHBOARD}/auth/app`,
  );
  assert.equal(url.searchParams.get("client_id"), CLIENT_ID);
  assert.equal(url.searchParams.get("prompt"), "consent");
  assert.equal(
    url.searchParams.get("max_age"),
    String(ACCOUNT_CONFIG.browserSessionMaxAgeSeconds),
  );
  assert.equal(url.searchParams.get("response_type"), "code");
  assert.equal(url.searchParams.get("code_challenge_method"), "S256");
  assert.match(
    url.searchParams.get("code_challenge") ?? "",
    /^[A-Za-z0-9_-]{43}$/,
  );
  assert.ok(url.searchParams.get("state"));
  const redirect = new URL(url.searchParams.get("redirect_uri") ?? "");
  assert.equal(redirect.protocol, "http:");
  assert.equal(redirect.hostname, "127.0.0.1");
  const port = Number(redirect.port);
  assert.ok(await listening(port), "loopback listener is up while signing in");
  await page.getByText("Continue in your browser").first().waitFor();
  if (screenshots)
    await page.screenshot({
      path: join(screenshots, "account-signing-in.png"),
    });

  // The dashboard accepts this client and loopback redirect and hands over to the
  // sign-in page on the identity host.
  const login = await fetch(authorize, { redirect: "follow" });
  const html = await login.text();
  assert.equal(login.status, 200, `authorize page answered ${login.status}`);
  assert.equal(
    `${new URL(login.url).origin}${new URL(login.url).pathname}`,
    `${ISSUER}/login`,
    `authorize ended at ${login.url} instead of the sign-in page`,
  );
  assert.match(html, /<form/i, "the sign-in page has no form");

  // Off again: sign-in is abandoned and the listener closes at once, with no restart.
  state = await account("setSwitches", { accountEnabled: false });
  assert.equal(state.accountEnabled, false);
  assert.equal(state.signingIn, false);
  await page.waitForTimeout(300);
  assert.equal(
    await listening(port),
    false,
    "loopback listener closed when switched off",
  );
  await corner
    .getByRole("button", { name: "Sign in" })
    .waitFor({ state: "detached" });
  if (screenshots)
    await page.screenshot({ path: join(screenshots, "account-off.png") });
  console.log("account sign-in live smoke passed");
} finally {
  await app.close();
}
