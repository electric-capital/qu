/**
 * The Quest mark: a hexagon ring of five slightly rounded segments with the
 * bottom-right sector filled by a full accent-coloured wedge.
 *
 * Every piece is filled by one shared, whisper-subtle diagonal wash: a
 * linear gradient from `--logo-grad-from` (top-left, slightly darker) to
 * `--logo-grad-to` (bottom-right, slightly lighter), so the mark brightens
 * toward the wedge. Both stops derive from `--accent` via color-mix (see
 * index.css), so the wash follows the active colour theme and light/dark
 * scheme. The gradient is userSpaceOnUse over the 48x48 viewBox and shared
 * by all three paths -- each piece samples the wash where it sits -- and the
 * ring's faded tints come from per-path fill-opacity (35% / 50%, alternating
 * for a little texture) multiplying the same gradient. The corner rounding
 * is baked into the path data (quadratic curves), NOT a stroke: the ring
 * fills are semi-transparent, and a same-colour stroke overlapping the fill
 * would double up into a visible outline.
 */

import { useId } from 'react';

/** Bottom, top-left and top-right segments (clockwise from the wedge). */
const SEGMENTS_A =
  'M33.01 41.31 Q33.76 42.61 32.26 42.61 L15.74 42.61 Q14.24 42.61 14.99 41.31 L18.74 34.82 Q19.49 33.52 20.99 33.52 L27.01 33.52 Q28.51 33.52 29.26 34.82 Z ' +
  'M4.50 23.15 Q3.00 23.15 3.75 21.85 L12.01 7.54 Q12.76 6.24 13.51 7.54 L17.26 14.03 Q18.01 15.33 17.26 16.63 L14.25 21.85 Q13.50 23.15 12.00 23.15 Z ' +
  'M34.49 7.54 Q35.24 6.24 35.99 7.54 L44.25 21.85 Q45.00 23.15 43.50 23.15 L36.00 23.15 Q34.50 23.15 33.75 21.85 L30.74 16.63 Q29.99 15.33 30.74 14.03 Z';

/** Bottom-left and top segments. */
const SEGMENTS_B =
  'M13.51 40.46 Q12.76 41.76 12.01 40.46 L3.75 26.15 Q3.00 24.85 4.50 24.85 L12.00 24.85 Q13.50 24.85 14.25 26.15 L17.26 31.37 Q18.01 32.67 17.26 33.97 Z ' +
  'M14.99 6.69 Q14.24 5.39 15.74 5.39 L32.26 5.39 Q33.76 5.39 33.01 6.69 L29.26 13.18 Q28.51 14.48 27.01 14.48 L20.99 14.48 Q19.49 14.48 18.74 13.18 Z';

const ACCENT_WEDGE =
  'M43.50 24.85 Q45.00 24.85 44.25 26.15 L35.99 40.46 Q35.24 41.76 34.49 40.46 L26.22 26.15 Q25.47 24.85 26.97 24.85 Z';

interface QuestLogoProps {
  className?: string;
}

export function QuestLogo({ className }: QuestLogoProps) {
  // Inline-SVG ids are document-global and the mark renders in several
  // places at once, so each instance defines the gradient under its own id
  // (useId stripped to url()-safe characters).
  const gradientId = `quest-logo-wash-${useId().replace(/\W/g, '')}`;
  const fill = `url(#${gradientId})`;
  return (
    <svg className={className} viewBox="0 0 48 48" aria-hidden="true">
      <defs>
        <linearGradient
          id={gradientId}
          gradientUnits="userSpaceOnUse"
          x1="7"
          y1="7"
          x2="41"
          y2="41"
        >
          <stop offset="0" stopColor="var(--logo-grad-from)" />
          <stop offset="1" stopColor="var(--logo-grad-to)" />
        </linearGradient>
      </defs>
      <path d={SEGMENTS_A} fill={fill} fillOpacity={0.35} />
      <path d={SEGMENTS_B} fill={fill} fillOpacity={0.5} />
      <path d={ACCENT_WEDGE} fill={fill} />
    </svg>
  );
}

/*
 * Geometry: 48x48 viewBox, hexagon centred at (24, 24), flat top/bottom,
 * outer radius 21, inner radius 10.5, radial gaps of 1.7 between pieces;
 * each corner is cut back 1.5 units along both edges and joined by a
 * quadratic curve through the original vertex. The accent wedge spans the
 * sector between the right and bottom-right vertices all the way to the
 * centre (its apex sits where the two gap-offset radial edges meet).
 * public/favicon.svg carries the same paths and wash with fixed colours.
 */
