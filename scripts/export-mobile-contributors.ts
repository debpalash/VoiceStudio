/** Native profile cards and portraits share Electron’s bundled source. */
import { readdir, mkdir } from "node:fs/promises";
import { homeMaintainerCards } from "../electron/src/renderer/src/features/home/home-maintainer-cards";

const output = new URL("../mobile/contract/maintainer-cards.json", import.meta.url);
const rendered = JSON.stringify(homeMaintainerCards, null, 2) + "\n";
if (process.argv.includes("--check")) {
  if ((await Bun.file(output).text()) !== rendered)
    throw new Error("Run bun scripts/export-mobile-contributors.ts");
} else {
  await Bun.write(output, rendered);
}

// iOS asset catalogs require image files beside their Contents.json. Android
// bundles the canonical directory directly through its Gradle catalog task.
const portraits = new URL(
  "../electron/src/renderer/src/features/home/contributors/",
  import.meta.url,
);
for (const name of await readdir(portraits)) {
  if (!name.endsWith(".png")) continue;
  const source = Bun.file(new URL(name, portraits));
  const directory = new URL(
    `../mobile/ios/VoiceStudio/Resources/Contributors.xcassets/contributor-${name.slice(0, -4)}.imageset/`,
    import.meta.url,
  );
  const destination = new URL(name, directory);
  const metadata =
    JSON.stringify(
      {
        images: [{ filename: name, idiom: "universal", scale: "3x" }],
        info: { author: "xcode", version: 1 },
      },
      null,
      2,
    ) + "\n";
  if (process.argv.includes("--check")) {
    if (
      !Buffer.from(await source.arrayBuffer()).equals(
        Buffer.from(await Bun.file(destination).arrayBuffer()),
      ) ||
      (await Bun.file(new URL("Contents.json", directory)).text()) !== metadata
    ) {
      throw new Error(`Run bun scripts/export-mobile-contributors.ts (${name})`);
    }
  } else {
    await mkdir(directory, { recursive: true });
    await Bun.write(destination, source);
    await Bun.write(new URL("Contents.json", directory), metadata);
  }
}
