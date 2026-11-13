import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { BellRingIcon } from 'lucide-react';

export type HomeSeason = 'christmas' | 'winter' | null;

/** Winter is December to February; Christmas week is 20–26 December (local date). */
export function homeSeason(date = new Date()): HomeSeason {
  const month = date.getMonth();
  const day = date.getDate();
  if (month === 11 && day >= 20 && day <= 26) return 'christmas';
  return month === 11 || month <= 1 ? 'winter' : null;
}

/** Today's season, or a preview set with localStorage `voicestudio.home.season` = `winter` | `christmas`. */
export function currentHomeSeason(): HomeSeason {
  try {
    const preview = localStorage.getItem('voicestudio.home.season');
    if (preview === 'winter' || preview === 'christmas') return preview;
  } catch {
    // Storage unavailable: use the calendar.
  }
  return homeSeason();
}

/** CSS pixels per snow pixel; the canvas renders at this lower resolution and scales up crisply. */
const CELL = 2;
const FRAME_MS = 1000 / 24;
const MAX_FLAKES = 140;

type Flake = {
  x: number;
  y: number;
  size: number;
  speed: number;
  phase: number;
  alpha: number;
};

/**
 * Quiet pixel snow behind the Home hero. It runs at 24 fps on a low-resolution
 * canvas, stops while the window is hidden or the hero is scrolled away, and
 * draws one still frame when the system asks for reduced motion.
 */
export function PixelSnow() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    let context: CanvasRenderingContext2D | null = null;
    try {
      context = canvas?.getContext('2d') ?? null;
    } catch {
      context = null;
    }
    if (!canvas || !context) return;
    const ctx = context;
    const motion = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    let still = motion?.matches ?? false;
    const readColor = () => getComputedStyle(canvas).color || 'rgb(255 255 255)';
    let color = readColor();
    let width = 1;
    let height = 1;
    let flakes: Flake[] = [];
    let frame = 0;
    let last = 0;
    let onScreen = true;

    const spawn = (anywhere: boolean): Flake => {
      const depth = Math.random();
      return {
        x: Math.random() * width,
        y: anywhere ? Math.random() * height : -2,
        size: depth > 0.94 ? 2 : 1,
        speed: 3 + depth * 8,
        phase: Math.random() * Math.PI * 2,
        alpha: 0.08 + depth * 0.3,
      };
    };
    const draw = () => {
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = color;
      for (const flake of flakes) {
        ctx.globalAlpha = flake.alpha;
        ctx.fillRect(Math.round(flake.x), Math.round(flake.y), flake.size, flake.size);
      }
    };
    const step = (seconds: number) => {
      for (let index = 0; index < flakes.length; index += 1) {
        const flake = flakes[index];
        flake.phase += seconds;
        flake.y += flake.speed * seconds;
        flake.x = (flake.x + Math.sin(flake.phase * 0.9) * 1.2 * seconds + width) % width;
        if (flake.y > height + 2) flakes[index] = spawn(false);
      }
    };
    const loop = (time: number) => {
      frame = requestAnimationFrame(loop);
      if (time - last < FRAME_MS) return;
      step(Math.min(0.1, (time - last) / 1000));
      last = time;
      draw();
    };
    const stop = () => {
      cancelAnimationFrame(frame);
      frame = 0;
    };
    const start = () => {
      if (still || frame || !onScreen || document.hidden) return;
      last = performance.now();
      frame = requestAnimationFrame(loop);
    };
    const resize = () => {
      width = Math.max(1, Math.round(canvas.clientWidth / CELL));
      height = Math.max(1, Math.round(canvas.clientHeight / CELL));
      canvas.width = width;
      canvas.height = height;
      const count = Math.min(MAX_FLAKES, Math.round((width * height) / 260));
      flakes = Array.from({ length: count }, () => spawn(true));
      draw();
    };
    const onVisibility = () => (document.hidden ? stop() : start());
    // Follow the system motion setting while Home stays open.
    const onMotion = (event: MediaQueryListEvent) => {
      still = event.matches;
      if (still) stop();
      else start();
    };

    resize();
    const resizer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(resize);
    resizer?.observe(canvas);
    const viewport =
      typeof IntersectionObserver === 'undefined'
        ? null
        : new IntersectionObserver(([entry]) => {
            onScreen = entry?.isIntersecting ?? true;
            if (onScreen) start();
            else stop();
          });
    viewport?.observe(canvas);
    // Light and dark themes tint the snow differently; follow theme switches.
    const theme =
      typeof MutationObserver === 'undefined'
        ? null
        : new MutationObserver(() => {
            color = readColor();
            draw();
          });
    theme?.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    document.addEventListener('visibilitychange', onVisibility);
    motion?.addEventListener?.('change', onMotion);
    start();
    return () => {
      stop();
      resizer?.disconnect();
      viewport?.disconnect();
      theme?.disconnect();
      document.removeEventListener('visibilitychange', onVisibility);
      motion?.removeEventListener?.('change', onMotion);
    };
  }, []);
  return <canvas ref={ref} className="home-snow" aria-hidden="true" data-testid="home-snow" />;
}

// 14×14 pixel Santa: hat with pompom, brim, face and beard.
const SANTA_ROWS = [
  '.........WW...',
  '........RWW...',
  '.......RR.....',
  '.....RRRR.....',
  '....RRRRRR....',
  '...WWWWWWWW...',
  '...SSSSSSSS...',
  '...SSBSSBSS...',
  '..WSSPSSPSSW..',
  '..WWWWSSWWWW..',
  '..WWWWWWWWWW..',
  '...WWWWWWWW...',
  '....WWWWWW....',
  '.....WWWW.....',
];
const SANTA_COLORS: Record<string, string> = {
  R: '#d64545',
  W: '#f4f4f5',
  S: '#f2c6a0',
  B: '#3f3f46',
  P: '#ec9a9a',
};
const SANTA_RECTS = SANTA_ROWS.flatMap((row, y) => {
  const runs: { x: number; y: number; width: number; fill: string }[] = [];
  for (let x = 0; x < row.length; x += 1) {
    const cell = row[x];
    if (cell === '.') continue;
    const previous = runs.at(-1);
    if (previous && previous.fill === SANTA_COLORS[cell] && previous.x + previous.width === x) {
      previous.width += 1;
    } else {
      runs.push({ x, y, width: 1, fill: SANTA_COLORS[cell] });
    }
  }
  return runs;
});

function PixelSanta() {
  return (
    <svg viewBox="0 0 14 14" width={28} height={28} shapeRendering="crispEdges" aria-hidden="true">
      {SANTA_RECTS.map(({ x, y, width, fill }) => (
        <rect key={`${x}-${y}`} x={x} y={y} width={width} height={1} fill={fill} />
      ))}
    </svg>
  );
}

/** Christmas week: a small bell and a pixel Santa who says "Ho ho ho!" now and then. */
export function HomeChristmas() {
  const { t } = useTranslation();
  return (
    <span className="home-christmas" aria-hidden="true" data-testid="home-christmas">
      <BellRingIcon className="home-christmas-bell" />
      <span className="home-christmas-santa">
        <PixelSanta />
        <span className="home-christmas-bubble">{t('homeUi.hoHoHo')}</span>
      </span>
    </span>
  );
}
