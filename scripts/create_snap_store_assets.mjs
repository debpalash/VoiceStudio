import { createHash } from "node:crypto";
import { copyFileSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const MAX_SCREENSHOTS = 5;

function pngDimensions(path) {
  const header = readFileSync(path).subarray(0, 24);
  if (header.length < 24 || header.toString("hex", 0, 8) !== "89504e470d0a1a0a") {
    throw new TypeError(`Snap Store asset must be a PNG: ${path}`);
  }
  return { width: header.readUInt32BE(16), height: header.readUInt32BE(20) };
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

function validateIcon(path) {
  const dimensions = pngDimensions(path);
  if (dimensions.width !== dimensions.height || dimensions.width > 512) {
    throw new RangeError(`Snap Store icon must be square and at most 512x512: ${path}`);
  }
  if (statSync(path).size > 256 * 1024) {
    throw new RangeError(`Snap Store icon exceeds 256 KiB: ${path}`);
  }
  return dimensions;
}

function validateScreenshot(path) {
  const dimensions = pngDimensions(path);
  const ratio = dimensions.width / dimensions.height;
  if (
    dimensions.width < 480 ||
    dimensions.height < 480 ||
    dimensions.width > 3840 ||
    dimensions.height > 2160 ||
    ratio < 0.5 ||
    ratio > 2
  ) {
    throw new RangeError(`Snap Store screenshot dimensions are unsupported: ${path}`);
  }
  if (statSync(path).size > 2 * 1024 * 1024) {
    throw new RangeError(`Snap Store screenshot exceeds 2 MiB: ${path}`);
  }
  return dimensions;
}

// Snapcraft listing banner: 3:1, 720x240 to 4320x1440, at most 2 MB.
function validateBanner(path) {
  const dimensions = pngDimensions(path);
  if (
    dimensions.width < 720 ||
    dimensions.width > 4320 ||
    dimensions.width !== 3 * dimensions.height
  ) {
    throw new RangeError(`Snap Store banner must be 3:1, 720x240 to 4320x1440: ${path}`);
  }
  if (statSync(path).size > 2 * 1024 * 1024) {
    throw new RangeError(`Snap Store banner exceeds 2 MiB: ${path}`);
  }
  return dimensions;
}

export function createSnapStoreAssets({
  assetsPath = resolve(root, "store/assets.json"),
  outputDir = resolve(root, "electron/release/snap-store-assets"),
} = {}) {
  const assets = JSON.parse(readFileSync(assetsPath, "utf8"));
  const sourceRoot = dirname(dirname(assetsPath));
  const screenshots = assets.screenshots.slice(0, MAX_SCREENSHOTS);
  if (!screenshots.length) throw new Error("Snap Store requires at least one screenshot");
  mkdirSync(outputDir, { recursive: true });

  const iconSource = resolve(sourceRoot, assets.iconSource);
  const iconDimensions = validateIcon(iconSource);
  const iconFile = "icon.png";
  copyFileSync(iconSource, resolve(outputDir, iconFile));

  let banner;
  if (assets.snapBannerSource) {
    const bannerSource = resolve(sourceRoot, assets.snapBannerSource);
    const bannerDimensions = validateBanner(bannerSource);
    copyFileSync(bannerSource, resolve(outputDir, "banner.png"));
    banner = {
      file: "banner.png",
      source: assets.snapBannerSource,
      ...bannerDimensions,
      bytes: statSync(bannerSource).size,
      sha256: sha256(bannerSource),
    };
  }

  const generatedScreenshots = screenshots.map((entry, index) => {
    const source = resolve(sourceRoot, entry.source);
    const dimensions = validateScreenshot(source);
    const file = `screenshot-${String(index + 1).padStart(2, "0")}-${basename(
      entry.source,
      extname(entry.source),
    )}.png`;
    copyFileSync(source, resolve(outputDir, file));
    return {
      file,
      caption: entry.caption,
      source: entry.source,
      ...dimensions,
      bytes: statSync(source).size,
      sha256: sha256(source),
    };
  });

  const manifest = {
    schemaVersion: 1,
    canonicalSource: "store/assets.json",
    icon: {
      file: iconFile,
      source: assets.iconSource,
      ...iconDimensions,
      bytes: statSync(iconSource).size,
      sha256: sha256(iconSource),
    },
    ...(banner ? { banner } : {}),
    screenshots: generatedScreenshots,
  };
  writeFileSync(resolve(outputDir, "listing-assets.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  return manifest;
}

function option(name, fallback) {
  const prefix = `--${name}=`;
  return process.argv.find((value) => value.startsWith(prefix))?.slice(prefix.length) ?? fallback;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const outputDir = resolve(root, option("output-dir", "electron/release/snap-store-assets"));
  const manifest = createSnapStoreAssets({ outputDir });
  console.log(
    `Wrote ${manifest.screenshots.length} screenshots, icon${manifest.banner ? " and banner" : ""} to ${outputDir}`,
  );
}
