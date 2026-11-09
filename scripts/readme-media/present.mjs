import { readFile, writeFile, stat } from "node:fs/promises";
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { resolve, join, basename } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("../..", import.meta.url)));
const require = createRequire(join(root, "electron/package.json"));
const dataUrl = (mime, bytes) => `data:${mime};base64,${bytes.toString("base64")}`;

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ["ignore", "ignore", "pipe"] });
    let error = "";
    child.stderr.on("data", (chunk) => {
      error = (error + chunk).slice(-6000);
    });
    child.on("error", reject);
    child.on("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(`${command} exited ${code}: ${error}`)),
    );
  });
}

/** Frame untouched renderer captures, then encode a quiet looping feature tour. */
export async function presentReadmeMedia(browser, captures, output, scratch) {
  const [template, background, logo, font] = await Promise.all([
    readFile(new URL("./presentation.html", import.meta.url), "utf8"),
    readFile(join(root, "docs/media/electron/presentation-background.svg"), "utf8"),
    readFile(join(root, "docs/logo.svg")),
    readFile(require.resolve("@fontsource-variable/inter/files/inter-latin-wght-normal.woff2")),
  ]);
  const html = template
    .replace("__BACKGROUND__", background)
    .replace("__LOGO__", dataUrl("image/svg+xml", logo))
    .replace("__FONT__", dataUrl("font/woff2", font));
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 2,
  });
  try {
    await page.setContent(html, { waitUntil: "load" });
    await page.evaluate(() => document.fonts.ready);
    for (const [index, capture] of captures.entries()) {
      const image = dataUrl("image/png", await readFile(capture.path));
      await page.evaluate(
        async ({ image, title, index }) => {
          document.querySelector("#feature").textContent = title;
          const img = document.querySelector("#capture");
          img.src = image;
          await img.decode();
          document
            .querySelectorAll(".sections span")
            .forEach((node, i) => node.classList.toggle("active", i === index));
          document.querySelector(".sections").style.visibility = index < 4 ? "visible" : "hidden";
        },
        { image, title: capture.title, index },
      );
      await page.screenshot({ path: join(output, `${capture.name}.png`), animations: "disabled" });
      console.log(`Framed ${capture.name}: 2880 × 2000`);
    }
  } finally {
    await page.close();
  }

  for (const capture of captures) {
    await run(process.env.CWEBP_PATH || "cwebp", [
      "-quiet",
      "-lossless",
      "-z",
      "9",
      "-resize",
      "1440",
      "1000",
      join(output, `${capture.name}.png`),
      "-o",
      join(output, `${capture.name}.webp`),
    ]);
  }

  // Four core workflows form the tour; overview/setup are companion stills.
  const images = [...captures.slice(0, 4), captures[0]].map((capture) =>
    join(output, `${capture.name}.png`),
  );
  const args = ["-y", "-hide_banner", "-loglevel", "error"];
  images.forEach((path, i) => args.push("-loop", "1", "-t", i === 4 ? "0.65" : "3.8", "-i", path));
  const filters = images.map(
    (_, i) => `[${i}:v]fps=30,scale=1440:1000:flags=lanczos,format=yuv420p,settb=AVTB[v${i}]`,
  );
  filters.push("[v0][v1]xfade=transition=fade:duration=0.5:offset=3.3[x1]");
  filters.push("[x1][v2]xfade=transition=fade:duration=0.5:offset=6.6[x2]");
  filters.push("[x2][v3]xfade=transition=fade:duration=0.5:offset=9.9[x3]");
  filters.push("[x3][v4]xfade=transition=fade:duration=0.5:offset=13.2[tour]");
  const video = join(output, "voicestudio.mp4");
  console.log("Encoding the 30 fps HD tour");
  await run(process.env.FFMPEG_PATH || "ffmpeg", [
    ...args,
    "-filter_complex",
    filters.join(";"),
    "-map",
    "[tour]",
    "-an",
    "-c:v",
    "libx264",
    "-crf",
    "17",
    "-preset",
    "slow",
    "-pix_fmt",
    "yuv420p",
    "-movflags",
    "+faststart",
    video,
  ]);
  const gif = join(scratch, "tour.gif");
  console.log("Encoding the 16 fps looping GIF");
  await run(process.env.FFMPEG_PATH || "ffmpeg", [
    "-y",
    "-hide_banner",
    "-loglevel",
    "error",
    "-i",
    video,
    "-filter_complex",
    "fps=16,scale=1120:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=full:reserve_transparent=0[p];[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle",
    "-loop",
    "0",
    gif,
  ]);
  const finalGif = join(output, "voicestudio.gif");
  await run(process.env.GIFSICLE_PATH || "gifsicle", [
    "--optimize=3",
    "--careful",
    gif,
    "--output",
    finalGif,
  ]);
  const sizes = {};
  for (const path of [
    ...captures.flatMap((capture) => [
      join(output, `${capture.name}.png`),
      join(output, `${capture.name}.webp`),
    ]),
    video,
    finalGif,
  ]) {
    sizes[basename(path)] = (await stat(path)).size;
  }
  await writeFile(join(scratch, "asset-sizes.json"), JSON.stringify(sizes, null, 2) + "\n");
  return sizes;
}
