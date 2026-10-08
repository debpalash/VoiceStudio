import { chromium } from 'playwright';
import { execSync } from 'child_process';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const URL = 'http://localhost:5173';
const OUTPUT_DIR = path.join(__dirname, '..', 'pics');
const RAW_VIDEO = path.join(OUTPUT_DIR, 'raw_record.webm');
const FINAL_MP4 = path.join(OUTPUT_DIR, 'promo.mp4');
const FINAL_GIF = path.join(OUTPUT_DIR, 'promo.gif');

// Ensure output directory exists
if (!fs.existsSync(OUTPUT_DIR)) {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true });
}

async function run() {
  console.log('🎬 Starting Cinematic Promo Recording...');

  const browser = await chromium.launch({
    headless: false,
    args: [
      '--window-size=1920,1080',
      '--force-device-scale-factor=2', // retina
      '--disable-font-subpixel-positioning',
      '--disable-smooth-scrolling'
    ]
  });

  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2,
    recordVideo: {
      dir: OUTPUT_DIR,
      size: { width: 1920, height: 1080 }
    }
  });

  const page = await context.newPage();
  let recordingOffsetSeconds = 0;
  const scriptStartTime = Date.now();

  // Helper for cinematic pausing
  const wait = (ms) => new Promise(r => setTimeout(r, ms));

  // Helper for smooth zoom injections
  const cinematicZoom = async (selector, scale, xOffset = 0, yOffset = 0) => {
    await page.evaluate(({ selector, scale, xOffset, yOffset }) => {
      const el = document.querySelector('body');
      el.style.transition = 'transform 2s cubic-bezier(0.25, 1, 0.5, 1)';
      el.style.transformOrigin = `${xOffset}% ${yOffset}%`;
      el.style.transform = `scale(${scale})`;
    }, { selector, scale, xOffset, yOffset });
  };

  const resetZoom = async () => {
    await page.evaluate(() => {
      const el = document.querySelector('body');
      el.style.transform = 'scale(1)';
    });
  };

  try {
    console.log('📍 Navigating to VoiceStudio Launchpad...');
    await page.goto(URL, { waitUntil: 'load' });
    
    // Give it enough time to stabilize the layout
    await wait(2500); 

    console.log('📍 Switching to History tab in sidebar...');
    await page.locator('button:has-text("History")').first().click();
    await wait(1500);

    console.log('📍 Loading Project from History...');
    // Click the "Load" button on the first history item
    await page.locator('button:has-text("Load")').first().click();
    
    // Wait for the workspace to fully load, waveforms to draw, etc
    console.log('⏳ Waiting for dubbing workspace to render...');
    await wait(3500);

    // 🎥 Cinematic Zoom on the Video Player
    console.log('📍 Zooming on Video Playback...');
    // Assuming video is roughly in the top left/center of the main view
    await cinematicZoom('.video-player-container', 1.4, 20, 30);
    await wait(2000);

    // Play the video
    console.log('📍 Playing timeline...');
    // Click play button (WaveSurfer play or native)
    // Finding the play button by its lucide icon class or explicit text is best
    // Using a broad selector for the play button in the waveform bar
    const playBtn = page
        .locator('button:has(.lucide-play), [aria-label="Playback controls"] button')
        .first();
    if (await playBtn.isVisible()) {
        await playBtn.click();
    }
    
    // Let it play for a bit while zoomed
    await wait(6000);

    // Reset zoom and zoom into the segment table
    console.log('📍 Highlighting Segment Controls...');
    await resetZoom();
    await wait(1500);
    await cinematicZoom('.segment-table', 1.3, 80, 50);
    await wait(3000);

    // Highlight Output Options
    console.log('📍 Showing Export Options...');
    await resetZoom();
    await wait(1000);
    await cinematicZoom('text=Output Options', 1.5, 50, 95);
    await wait(3000);

    // Reset zoom
    await resetZoom();
    await wait(1500);

    // Show Clone Tab
    console.log('📍 Navigating Tabs (Clone, Design)...');
    await page.click('text=Clone');
    await wait(2500);

    // Show Design Tab
    console.log('📍 Showing Voice Design...');
    await page.click('text=Design');
    await wait(2500);

    // Back to Dub
    await page.click('text=Dub');
    await wait(2000);

    console.log('✅ Browser automation complete.');
  } catch (error) {
    console.error('❌ Error during automation:', error);
  } finally {
    // Stop recording and close
    const videoPath = await page.video().path();
    await context.close();
    await browser.close();

    // Rename the raw recording
    if (fs.existsSync(videoPath)) {
      fs.renameSync(videoPath, RAW_VIDEO);
      console.log(`\n💾 Raw recording saved to: ${RAW_VIDEO}`);
      
      console.log('✂️  Processing with FFmpeg (Trimming, Speeding, GIF Generation)...');
      try {
        console.log(`✂️  Processing final MP4 without trimming...`);
        
        // Generate MP4 Promo (Lossless quality)
        // preset=veryslow, crf=16 is virtually lossless
        // Speed up the entire video slightly (0.85*PTS is about 15% faster) since UI interactions can feel slow on playback
        execSync(`ffmpeg -y -i "${RAW_VIDEO}" -filter_complex "[0:v]setpts=0.85*PTS[v]" -map "[v]" -c:v libx264 -preset veryslow -crf 16 -pix_fmt yuv420p "${FINAL_MP4}"`, { stdio: 'inherit' });
        console.log(`\n✨ HIGH QUALITY MP4 generated: ${FINAL_MP4}`);

        // Generate GIF Promo (24fps, 1080p width, high quality palette)
        execSync(`ffmpeg -y -i "${FINAL_MP4}" -vf "fps=24,scale=1080:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=255:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5" -loop 0 "${FINAL_GIF}"`, { stdio: 'inherit' });
        console.log(`✨ HIGH QUALITY GIF generated: ${FINAL_GIF}`);
        
        // Clean up raw
        fs.unlinkSync(RAW_VIDEO);

      } catch (e) {
        console.error('❌ FFmpeg processing failed. Please ensure ffmpeg is installed natively.');
        console.error(e.message);
      }

    }
  }
}

run();
