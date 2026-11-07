import rawConfig from "./account.json" with { type: "json" };

// Public sign-in endpoints and client IDs only: never secrets. Every build reads this file,
// so CI and release builds offer sign-in without any build-time flag; each user still turns
// account features on themselves (off by default).

const fail = (field) => {
  throw new TypeError(`config/account.json: invalid ${field}`);
};

function exactKeys(value, names, path) {
  if (
    !value ||
    typeof value !== "object" ||
    Array.isArray(value) ||
    Object.keys(value).length !== names.length ||
    !names.every((name) => Object.hasOwn(value, name))
  )
    fail(path);
}

function url(value, path, allowLoopbackHttp) {
  if (typeof value !== "string" || value !== value.trim() || !value) fail(path);
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    fail(path);
  }
  const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname);
  if (
    (parsed.protocol !== "https:" &&
      !(allowLoopbackHttp && loopback && parsed.protocol === "http:")) ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    value.endsWith("/")
  )
    fail(path);
}

const ENVIRONMENT_NAMES = ["prod", "dev", "local"];
const ENDPOINT_NAMES = ["issuer", "accountApi", "syncApi", "dashboard"];

/** Validates the shape so a bad edit fails the build, not a user's sign-in. */
export function parseAccountConfig(input) {
  exactKeys(input, ["schemaVersion", "browserSessionMaxAgeSeconds", "environments"], "root fields");
  if (input.schemaVersion !== 1) fail("schemaVersion");
  const maxAge = input.browserSessionMaxAgeSeconds;
  if (!Number.isInteger(maxAge) || maxAge < 60 || maxAge > 30 * 86400)
    fail("browserSessionMaxAgeSeconds");
  exactKeys(input.environments, ENVIRONMENT_NAMES, "environments");
  for (const name of ENVIRONMENT_NAMES) {
    const env = input.environments[name];
    exactKeys(env, [...ENDPOINT_NAMES, "clientId"], `environments.${name}`);
    for (const key of ENDPOINT_NAMES) url(env[key], `environments.${name}.${key}`, name === "local");
    // Zitadel client IDs are numeric; only a local stack may leave it empty (set at run time).
    if (typeof env.clientId !== "string" || !(/^\d{6,32}$/.test(env.clientId) || (name === "local" && env.clientId === "")))
      fail(`environments.${name}.clientId`);
  }
  return deepFreeze(structuredClone(input));
}

function deepFreeze(value) {
  if (value && typeof value === "object") {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

export const ACCOUNT_CONFIG = parseAccountConfig(rawConfig);
