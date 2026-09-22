/**
 * The "Made with ♥ by Electric Capital" glyph: a monochrome (currentColor)
 * heart that morphs into the Electric lightning bolt and back on a 14s loop
 * -- 5s resting as the heart, 2s morph, 5s resting as the bolt, 2s morph
 * back.
 *
 * The bolt is the Electric Capital mark: tall and narrow (about 1:2.7) with
 * the two inner step edges exactly vertical, spanning nearly the full 24-unit
 * box height. The heart is drawn smaller inside the same box (about 13 units
 * wide) so it reads at text size while the bolt gets the height it needs;
 * callers render the box taller than the line and pull it onto the text's
 * vertical centre (see the `.about-heart` / `.home-composer-footer-heart`
 * rules).
 *
 * Both shapes are ONE closed path made of the same eight cubic-bezier
 * segments so the browser can interpolate the `d` attribute point-for-point
 * (SMIL `<animate>` -- supported by every evergreen browser, unlike the CSS
 * `d` property which Safari lacks). The anchors are ordered the same way
 * around both outlines (top, left, bottom, right) so the morph reads as a
 * rotation-free squash rather than a twist: notch->bolt tip, left lobe->
 * bolt's left corner, heart tip->bolt tail, right lobe->bolt's right corner.
 * The bolt's straight edges are cubics with collinear control points at 1/3
 * and 2/3 of each edge.
 *
 * Users who prefer reduced motion get the static heart.
 */

import { useSyncExternalStore } from 'react';

const HEART =
  'M12 8.5 ' +
  'C12 6.82 10.6 5.7 8.85 5.7 ' +
  'C6.96 5.7 5.35 7.31 5.35 9.2 ' +
  'C5.35 10.95 6.26 12.42 7.45 13.75 ' +
  'C8.85 15.29 10.6 16.76 12 18.3 ' +
  'C13.4 16.76 15.15 15.29 16.55 13.75 ' +
  'C17.74 12.42 18.65 10.95 18.65 9.2 ' +
  'C18.65 7.31 17.04 5.7 15.15 5.7 ' +
  'C13.4 5.7 12 6.82 12 8.5 Z';

const BOLT =
  'M12 1 ' +
  'C11.267 3.067 10.533 5.133 9.8 7.2 ' +
  'C9.067 9.267 8.333 11.333 7.6 13.4 ' +
  'C9.067 13.4 10.533 13.4 12 13.4 ' +
  'C12 16.6 12 19.8 12 23 ' +
  'C12.733 20.933 13.467 18.867 14.2 16.8 ' +
  'C14.933 14.733 15.667 12.667 16.4 10.6 ' +
  'C14.933 10.6 13.467 10.6 12 10.6 ' +
  'C12 7.4 12 4.2 12 1 Z';

const HOLD_S = 5;
const MORPH_S = 2;
const CYCLE_S = 2 * (HOLD_S + MORPH_S);
const fraction = (s: number) => (s / CYCLE_S).toFixed(4);

// heart (hold) -> bolt (hold) -> heart
const VALUES = [HEART, HEART, BOLT, BOLT, HEART].join(';');
const KEY_TIMES = [0, HOLD_S, HOLD_S + MORPH_S, 2 * HOLD_S + MORPH_S, CYCLE_S]
  .map(fraction)
  .join(';');
// One spline per interval: linear (no-op) across the holds, ease-in-out
// across the morphs.
const HOLD_SPLINE = '0 0 1 1';
const MORPH_SPLINE = '0.42 0 0.58 1';
const KEY_SPLINES = [HOLD_SPLINE, MORPH_SPLINE, HOLD_SPLINE, MORPH_SPLINE].join(';');

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

function subscribeReducedMotion(callback: () => void): () => void {
  if (typeof window.matchMedia !== 'function') return () => {};
  const mql = window.matchMedia(REDUCED_MOTION_QUERY);
  mql.addEventListener('change', callback);
  return () => mql.removeEventListener('change', callback);
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

interface HeartBoltMorphProps {
  className?: string;
  /** Rendered width/height in px; the glyph is drawn on a 24x24 viewBox. */
  size?: number;
}

export function HeartBoltMorph({ className, size = 18 }: HeartBoltMorphProps) {
  const reducedMotion = useSyncExternalStore(
    subscribeReducedMotion,
    prefersReducedMotion,
    () => false,
  );
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      role="img"
      aria-label="love"
    >
      <path d={HEART}>
        {!reducedMotion && (
          <animate
            attributeName="d"
            dur={`${CYCLE_S}s`}
            repeatCount="indefinite"
            values={VALUES}
            keyTimes={KEY_TIMES}
            calcMode="spline"
            keySplines={KEY_SPLINES}
          />
        )}
      </path>
    </svg>
  );
}
