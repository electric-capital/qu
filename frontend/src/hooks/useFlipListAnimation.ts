/**
 * FLIP-animate reorders in a keyed list.
 *
 * Attach the returned ref to the list container and give every animatable
 * child a `data-flip-id` attribute. After every commit, children that
 * existed before and moved vertically glide from their previous position
 * to the new one instead of snapping -- e.g. a sidebar conversation
 * jumping to the top when new activity re-sorts the list. Newly mounted
 * children (no previous measurement) and removed children are left alone.
 *
 * Measures on every render (no dep array) on purpose: the rendered order
 * can change without any one prop changing identity (client-side filter
 * passes, inline rename swaps), and re-measuring keeps the position
 * snapshot fresh so the next real reorder animates from where rows
 * actually were. The cost is a few dozen getBoundingClientRect calls per
 * commit, which is negligible for a sidebar-sized list.
 *
 * Positions are measured relative to the container, not the viewport, so a
 * user scrolling the panel between renders can't smear a phantom offset
 * across every row. Uses the Web Animations API (element.animate), which
 * needs no CSS coordination and self-cleans; respects
 * `prefers-reduced-motion`.
 */

import { useLayoutEffect, useRef } from 'react';

const ANIMATION_MS = 250;

export function useFlipListAnimation<T extends HTMLElement>() {
  const containerRef = useRef<T | null>(null);
  // data-flip-id -> container-relative top from the previous render
  const prevTopsRef = useRef<Map<string, number>>(new Map());

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const containerTop = container.getBoundingClientRect().top;
    const items = Array.from(
      container.querySelectorAll<HTMLElement>('[data-flip-id]')
    );
    const newTops = new Map<string, number>();
    for (const el of items) {
      newTops.set(
        el.dataset.flipId as string,
        el.getBoundingClientRect().top - containerTop
      );
    }

    const reduceMotion = window.matchMedia(
      '(prefers-reduced-motion: reduce)'
    ).matches;
    if (!reduceMotion && typeof container.animate === 'function') {
      for (const el of items) {
        const id = el.dataset.flipId as string;
        const prev = prevTopsRef.current.get(id);
        if (prev === undefined) continue; // newly mounted -- nothing to glide from
        const dy = prev - (newTops.get(id) as number);
        if (Math.abs(dy) < 1) continue;
        el.animate(
          [{ transform: `translateY(${dy}px)` }, { transform: 'translateY(0)' }],
          { duration: ANIMATION_MS, easing: 'ease-in-out' }
        );
      }
    }

    prevTopsRef.current = newTops;
  }); // intentionally no dep array -- see module docstring

  return containerRef;
}
