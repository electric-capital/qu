/**
 * In-modal PDF preview built directly on pdfjs-dist (no wrapper library).
 *
 * Two-pane layout: a left thumbnail rail (click to navigate, current page
 * highlighted) and a main fit-to-width page scroller. Pages and thumbnails
 * render lazily into canvases via IntersectionObservers so large documents
 * don't kick off hundreds of render tasks at open.
 *
 * All pdf.js runtime assets are self-hosted: the deployment has no network
 * egress, so the worker is bundled via Vite's ?url import and the
 * cmaps/standard_fonts/wasm/iccs directories are copied into
 * dist/assets/pdfjs/ by vite-plugin-static-copy (see vite.config.ts). Every
 * asset URL below must stay same-origin.
 */

import { useState, useEffect, useLayoutEffect, useRef, useCallback } from 'react';
import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist';
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import './PdfViewer.css';

// The worker comes from the same installed pdfjs-dist copy as the main-thread
// library, so the versions can never mismatch.
GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

// Same-origin asset locations (copied to dist/assets/pdfjs/ at build time).
// Centralized in one constant so no second getDocument call site can drift.
const PDF_DOCUMENT_OPTIONS = {
  cMapUrl: '/assets/pdfjs/cmaps/',
  cMapPacked: true,
  standardFontDataUrl: '/assets/pdfjs/standard_fonts/',
  wasmUrl: '/assets/pdfjs/wasm/',
  iccUrl: '/assets/pdfjs/iccs/',
};

const THUMBNAIL_WIDTH = 120; // CSS px
const PAGE_GAP = 16; // matches the main-view flex gap in PdfViewer.css
const MAIN_PADDING_X = 24; // matches the main-view horizontal padding

// Cap the canvas backing-store multiplier to bound bitmap memory on
// high-density displays.
function getDpr(): number {
  return Math.min(window.devicePixelRatio || 1, 2);
}

interface PageSize {
  width: number;
  height: number;
}

interface PdfViewerProps {
  data: ArrayBuffer;
  fileName: string;
  onMetadata?: (info: { numPages: number }) => void;
}

export function PdfViewer({ data, fileName, onMetadata }: PdfViewerProps) {
  const [numPages, setNumPages] = useState(0);
  // Scale-1 page dimensions, index = pageNumber - 1.
  const [pageSizes, setPageSizes] = useState<PageSize[]>([]);
  const [docReady, setDocReady] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [mainWidth, setMainWidth] = useState(0);
  const [pageErrors, setPageErrors] = useState<Set<number>>(new Set());

  const docRef = useRef<PDFDocumentProxy | null>(null);
  const pageProxies = useRef(new Map<number, PDFPageProxy>());
  const mainScrollRef = useRef<HTMLDivElement>(null);
  const railRef = useRef<HTMLDivElement>(null);
  const pageContainerRefs = useRef(new Map<number, HTMLDivElement>());
  const pageCanvasRefs = useRef(new Map<number, HTMLCanvasElement>());
  const thumbContainerRefs = useRef(new Map<number, HTMLButtonElement>());
  const thumbCanvasRefs = useRef(new Map<number, HTMLCanvasElement>());
  // In-flight render tasks and the scale each canvas was last rendered at,
  // keyed by "main:N" / "thumb:N".
  const renderTasks = useRef(new Map<string, RenderTask>());
  const renderedScales = useRef(new Map<string, number>());
  const visiblePages = useRef(new Set<number>());
  const visibleThumbs = useRef(new Set<number>());
  const cancelledRef = useRef(false);
  const pageSizesRef = useRef<PageSize[]>([]);
  const mainWidthRef = useRef(0);
  const onMetadataRef = useRef(onMetadata);
  onMetadataRef.current = onMetadata;

  // Keep ref mirrors in sync for use inside observer callbacks (declared
  // before the observer effect so they run first).
  useEffect(() => {
    pageSizesRef.current = pageSizes;
  }, [pageSizes]);
  useEffect(() => {
    mainWidthRef.current = mainWidth;
  }, [mainWidth]);

  const fitScaleFor = useCallback((pageNum: number): number => {
    const base = pageSizesRef.current[pageNum - 1];
    if (!base) return 1;
    const available = mainWidthRef.current - MAIN_PADDING_X * 2;
    if (available <= 0) return 1;
    return Math.max(available / base.width, 0.1);
  }, []);

  const getPageProxy = useCallback(async (pageNum: number): Promise<PDFPageProxy> => {
    const cached = pageProxies.current.get(pageNum);
    if (cached) return cached;
    const doc = docRef.current;
    if (!doc) throw new Error('Document not loaded');
    const page = await doc.getPage(pageNum);
    pageProxies.current.set(pageNum, page);
    return page;
  }, []);

  // Render one page (or thumbnail) into its canvas. Cancels any in-flight
  // render for the same canvas first; RenderingCancelledException is expected
  // and swallowed.
  const renderPageCanvas = useCallback(async (kind: 'main' | 'thumb', pageNum: number) => {
    if (cancelledRef.current || !docRef.current) return;
    const canvas = (kind === 'main' ? pageCanvasRefs : thumbCanvasRefs).current.get(pageNum);
    const base = pageSizesRef.current[pageNum - 1];
    if (!canvas || !base) return;

    const cssScale = kind === 'main' ? fitScaleFor(pageNum) : THUMBNAIL_WIDTH / base.width;
    const dpr = getDpr();
    const renderScale = cssScale * dpr;
    const key = `${kind}:${pageNum}`;
    if (renderedScales.current.get(key) === renderScale) return;

    const inFlight = renderTasks.current.get(key);
    if (inFlight) inFlight.cancel();

    let task: RenderTask | null = null;
    try {
      const page = await getPageProxy(pageNum);
      if (cancelledRef.current) return;
      const viewport = page.getViewport({ scale: renderScale });
      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      canvas.style.width = `${Math.floor(viewport.width / dpr)}px`;
      canvas.style.height = `${Math.floor(viewport.height / dpr)}px`;
      task = page.render({ canvas, viewport });
      renderTasks.current.set(key, task);
      await task.promise;
      if (renderTasks.current.get(key) === task) renderTasks.current.delete(key);
      renderedScales.current.set(key, renderScale);
    } catch (err) {
      // Only clear our own registration: a newer render for the same canvas
      // may have replaced it after cancelling this one.
      if (task && renderTasks.current.get(key) === task) renderTasks.current.delete(key);
      if ((err as Error | undefined)?.name === 'RenderingCancelledException') return;
      if (cancelledRef.current) return;
      if (kind === 'main') {
        setPageErrors((prev) => {
          const next = new Set(prev);
          next.add(pageNum);
          return next;
        });
      }
    }
  }, [fitScaleFor, getPageProxy]);

  // Evict a far-off-screen main canvas: zero the backing store to free bitmap
  // memory; it re-renders when scrolled back near. Thumbnails are small
  // enough to keep forever.
  const evictMainCanvas = useCallback((pageNum: number) => {
    const key = `main:${pageNum}`;
    const inFlight = renderTasks.current.get(key);
    if (inFlight) {
      inFlight.cancel();
      renderTasks.current.delete(key);
    }
    if (!renderedScales.current.has(key)) return;
    renderedScales.current.delete(key);
    const canvas = pageCanvasRefs.current.get(pageNum);
    if (canvas) {
      canvas.width = 0;
      canvas.height = 0;
    }
  }, []);

  // Load the document. Re-runs when the bytes change (i.e. a new file).
  useEffect(() => {
    let cancelled = false;
    cancelledRef.current = false;
    setLoadError(null);
    setDocReady(false);
    setNumPages(0);
    setPageSizes([]);
    setPageErrors(new Set());
    setCurrentPage(1);
    renderedScales.current.clear();
    visiblePages.current.clear();
    visibleThumbs.current.clear();

    // Copy the buffer before handing it to pdf.js: getDocument transfers the
    // bytes to the worker (neutering the source), and React StrictMode
    // double-invokes this effect with the same ArrayBuffer.
    const bytes = new Uint8Array(data.slice(0));
    const loadingTask = getDocument({ data: bytes, ...PDF_DOCUMENT_OPTIONS });

    (async () => {
      try {
        const doc = await loadingTask.promise;
        if (cancelled) return;
        docRef.current = doc;
        setNumPages(doc.numPages);
        onMetadataRef.current?.({ numPages: doc.numPages });

        const first = await doc.getPage(1);
        if (cancelled) return;
        pageProxies.current.set(1, first);
        const vp1 = first.getViewport({ scale: 1 });
        const defaultSize: PageSize = { width: vp1.width, height: vp1.height };

        // Assume page-1 dimensions for all pages so layout is available
        // immediately, then sweep the real metadata (getPage + getViewport is
        // metadata-only, no raster — fast for the document sizes we expect)
        // and correct in one state update. Tradeoff: mixed-size documents see
        // a layout shift when the sweep lands; the alternative (per-page lazy
        // measurement) is more complex for little gain.
        const sizes: PageSize[] = new Array(doc.numPages).fill(defaultSize);
        pageSizesRef.current = sizes;
        setPageSizes(sizes);
        setDocReady(true);

        if (doc.numPages > 1) {
          const corrected = sizes.slice();
          for (let i = 2; i <= doc.numPages; i++) {
            const page = await doc.getPage(i);
            if (cancelled) return;
            pageProxies.current.set(i, page);
            const vp = page.getViewport({ scale: 1 });
            corrected[i - 1] = { width: vp.width, height: vp.height };
          }
          pageSizesRef.current = corrected;
          setPageSizes(corrected);
          // Pages already rendered with assumed sizes get a corrected pass.
          visiblePages.current.forEach((n) => void renderPageCanvas('main', n));
          visibleThumbs.current.forEach((n) => void renderPageCanvas('thumb', n));
        }
      } catch (err) {
        if (cancelled) return;
        const name = (err as Error | undefined)?.name;
        if (name === 'PasswordException') {
          setLoadError("This PDF is password-protected and can't be previewed.");
        } else {
          setLoadError('This file could not be opened as a PDF.');
        }
      }
    })();

    return () => {
      cancelled = true;
      cancelledRef.current = true;
      renderTasks.current.forEach((task) => task.cancel());
      renderTasks.current.clear();
      renderedScales.current.clear();
      pageProxies.current.clear();
      docRef.current = null;
      // destroy() tears down the document proxy and the worker round-trips
      // for this document.
      loadingTask.destroy().catch(() => {});
    };
  }, [data, renderPageCanvas]);

  // Measure the main scroller as soon as it mounts so page widths are correct
  // on first paint.
  useLayoutEffect(() => {
    if (docReady && mainScrollRef.current) {
      setMainWidth(mainScrollRef.current.clientWidth);
    }
  }, [docReady]);

  // Track scroller width changes (debounced); visible pages re-render at the
  // new fit-to-width scale via the observer effect below.
  useEffect(() => {
    if (!docReady) return;
    const el = mainScrollRef.current;
    if (!el) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const observer = new ResizeObserver(() => {
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        setMainWidth(el.clientWidth);
      }, 150);
    });
    observer.observe(el);
    return () => {
      observer.disconnect();
      if (timer !== null) clearTimeout(timer);
    };
  }, [docReady]);

  // Lazy rendering + eviction. Recreated when the width changes so the
  // initial callbacks re-fire and visible canvases re-render at the new
  // scale (renderPageCanvas skips canvases already at the right scale).
  useEffect(() => {
    if (!docReady || numPages === 0 || mainWidth === 0) return;
    const scroller = mainScrollRef.current;
    const rail = railRef.current;
    if (!scroller || !rail) return;

    const pageOf = (el: Element): number => Number((el as HTMLElement).dataset.page);

    // Render pages within ~1 viewport of visibility.
    const renderObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const n = pageOf(entry.target);
        if (entry.isIntersecting) {
          visiblePages.current.add(n);
          void renderPageCanvas('main', n);
        } else {
          visiblePages.current.delete(n);
        }
      }
    }, { root: scroller, rootMargin: '100% 0px' });

    // Free canvas bitmaps once a page drifts ~5 viewports away.
    const evictObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) evictMainCanvas(pageOf(entry.target));
      }
    }, { root: scroller, rootMargin: '500% 0px' });

    const thumbObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const n = pageOf(entry.target);
        if (entry.isIntersecting) {
          visibleThumbs.current.add(n);
          void renderPageCanvas('thumb', n);
        } else {
          visibleThumbs.current.delete(n);
        }
      }
    }, { root: rail, rootMargin: '200% 0px' });

    pageContainerRefs.current.forEach((el) => {
      renderObserver.observe(el);
      evictObserver.observe(el);
    });
    thumbContainerRefs.current.forEach((el) => thumbObserver.observe(el));

    return () => {
      renderObserver.disconnect();
      evictObserver.disconnect();
      thumbObserver.disconnect();
    };
  }, [docReady, numPages, mainWidth, renderPageCanvas, evictMainCanvas]);

  // Scroll-spy: the page with the greatest visible height in the main view is
  // the current page (rAF-throttled).
  useEffect(() => {
    if (!docReady) return;
    const scroller = mainScrollRef.current;
    if (!scroller) return;
    let raf = 0;
    const onScroll = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const scrollerRect = scroller.getBoundingClientRect();
        let best = 1;
        let bestVisible = -Infinity;
        pageContainerRefs.current.forEach((el, n) => {
          const rect = el.getBoundingClientRect();
          const visible = Math.min(rect.bottom, scrollerRect.bottom) - Math.max(rect.top, scrollerRect.top);
          if (visible > bestVisible || (visible === bestVisible && n < best)) {
            bestVisible = visible;
            best = n;
          }
        });
        setCurrentPage((prev) => (prev === best ? prev : best));
      });
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      scroller.removeEventListener('scroll', onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
  }, [docReady]);

  // Keep the active thumbnail visible as the main view scrolls.
  useEffect(() => {
    thumbContainerRefs.current.get(currentPage)?.scrollIntoView({ block: 'nearest' });
  }, [currentPage]);

  // Focus the scroller so PageUp/PageDown/arrow keys work without clicking.
  useEffect(() => {
    if (docReady) mainScrollRef.current?.focus();
  }, [docReady]);

  const scrollToPage = useCallback((pageNum: number) => {
    const el = pageContainerRefs.current.get(pageNum);
    const scroller = mainScrollRef.current;
    if (!el || !scroller) return;
    // Instant jump: smooth scrolling across long documents is worse.
    scroller.scrollTop = el.offsetTop - PAGE_GAP;
    setCurrentPage(pageNum);
  }, []);

  if (loadError) {
    return (
      <div className="pdf-viewer">
        <div className="file-viewer-error">{loadError}</div>
      </div>
    );
  }

  if (!docReady) {
    return (
      <div className="pdf-viewer">
        <div className="file-viewer-loading">Loading...</div>
      </div>
    );
  }

  const pageNumbers = Array.from({ length: numPages }, (_, i) => i + 1);
  const showPages = mainWidth > 0;

  return (
    <div className="pdf-viewer">
      <div className="pdf-thumbnail-rail" ref={railRef}>
        {pageNumbers.map((n) => {
          const size = pageSizes[n - 1];
          const thumbHeight = size ? Math.round((THUMBNAIL_WIDTH * size.height) / size.width) : THUMBNAIL_WIDTH;
          return (
            <button
              key={n}
              type="button"
              data-page={n}
              className={`pdf-thumbnail${n === currentPage ? ' pdf-thumbnail-active' : ''}`}
              onClick={() => scrollToPage(n)}
              title={`Page ${n}`}
              ref={(el) => {
                if (el) thumbContainerRefs.current.set(n, el);
                else thumbContainerRefs.current.delete(n);
              }}
            >
              <div className="pdf-thumbnail-canvas-wrap" style={{ width: THUMBNAIL_WIDTH, height: thumbHeight }}>
                <canvas
                  ref={(el) => {
                    if (el) thumbCanvasRefs.current.set(n, el);
                    else thumbCanvasRefs.current.delete(n);
                  }}
                />
              </div>
              <span className="pdf-thumbnail-label">{n}</span>
            </button>
          );
        })}
      </div>
      <div
        className="pdf-main-view"
        ref={mainScrollRef}
        tabIndex={-1}
        role="document"
        aria-label={fileName}
      >
        {showPages && pageNumbers.map((n) => {
          const size = pageSizes[n - 1];
          const scale = size ? Math.max((mainWidth - MAIN_PADDING_X * 2) / size.width, 0.1) : 1;
          const width = size ? Math.floor(size.width * scale) : 0;
          const height = size ? Math.floor(size.height * scale) : 0;
          return (
            <div
              key={n}
              data-page={n}
              className="pdf-page"
              style={{ width, height }}
              ref={(el) => {
                if (el) pageContainerRefs.current.set(n, el);
                else pageContainerRefs.current.delete(n);
              }}
            >
              {pageErrors.has(n) ? (
                <div className="pdf-page-error">Failed to render page {n}</div>
              ) : (
                <>
                  {/* Placeholder shows through until the (opaque) canvas render lands */}
                  <div className="pdf-page-placeholder">Page {n}</div>
                  <canvas
                    className="pdf-page-canvas"
                    ref={(el) => {
                      if (el) pageCanvasRefs.current.set(n, el);
                      else pageCanvasRefs.current.delete(n);
                    }}
                  />
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
