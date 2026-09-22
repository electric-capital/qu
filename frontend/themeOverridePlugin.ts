// PostCSS plugin backing the Settings > Appearance theme selector.
//
// Every component stylesheet in this app themes itself with plain
// `@media (prefers-color-scheme: light) { ... }` blocks, and a media query
// cannot be overridden from JavaScript. Instead of rewriting ~50 files, this
// build-time plugin turns each such block into two copies:
//
//   1. the ORIGINAL media block, with every selector scoped to
//      `:root:not([data-theme="dark"])` -- i.e. "auto" mode (no data-theme
//      attribute) still follows the OS, and a forced dark theme suppresses
//      the light overrides;
//   2. an UNWRAPPED copy scoped to `:root[data-theme="light"]` -- a forced
//      light theme applies the overrides regardless of the OS setting.
//
// The scope is added through `:where()`, which carries zero specificity, so
// the rewritten rules keep exactly the specificity and source order of the
// originals and the cascade is unchanged. `prefers-color-scheme: dark`
// blocks are handled symmetrically. `src/utils/theme.ts` owns the
// data-theme attribute; `index.html` sets it from localStorage before the
// first paint.
import type { AtRule, ChildNode, Plugin, Rule } from 'postcss';

type Scheme = 'light' | 'dark';

const SCHEME_CLAUSE = /\(\s*prefers-color-scheme\s*:\s*(light|dark)\s*\)/i;

function detectScheme(params: string): Scheme | null {
  const match = SCHEME_CLAUSE.exec(params);
  return match ? (match[1].toLowerCase() as Scheme) : null;
}

// Remove the prefers-color-scheme clause (and a dangling `and`) from a media
// query list, e.g. "(max-width: 768px) and (prefers-color-scheme: light)" ->
// "(max-width: 768px)". Returns "" when the clause was the whole query.
export function stripSchemeClause(params: string): string {
  return params
    .replace(/\s+and\s+\(\s*prefers-color-scheme\s*:\s*(?:light|dark)\s*\)/i, '')
    .replace(/\(\s*prefers-color-scheme\s*:\s*(?:light|dark)\s*\)\s+and\s+/i, '')
    .replace(SCHEME_CLAUSE, '')
    .trim();
}

// Scope one selector to the given :root qualifier without changing its
// specificity. `:root`/`html` selectors get the qualifier on the root itself.
export function scopeSelector(selector: string, rootQualifier: string): string {
  const s = selector.trim();
  const rootMatch = /^(:root|html)(?![\w-])/.exec(s);
  if (rootMatch) {
    return `${rootMatch[1]}:where(${rootQualifier})${s.slice(rootMatch[1].length)}`;
  }
  return `:where(:root${rootQualifier}) ${s}`;
}

function isKeyframeStep(rule: Rule): boolean {
  const parent = rule.parent;
  return parent?.type === 'atrule' && /keyframes$/i.test((parent as AtRule).name);
}

function scopeRules(container: AtRule, rootQualifier: string): void {
  container.walkRules((rule) => {
    if (isKeyframeStep(rule)) return;
    rule.selectors = rule.selectors.map((sel) => scopeSelector(sel, rootQualifier));
  });
}

export function themeOverridePlugin(): Plugin {
  return {
    postcssPlugin: 'quest-theme-override',
    Once(root, { result }) {
      const targets: AtRule[] = [];
      root.walkAtRules('media', (atRule) => {
        if (detectScheme(atRule.params)) targets.push(atRule);
      });

      for (const atRule of targets) {
        const scheme = detectScheme(atRule.params)!;
        if (atRule.params.includes(',')) {
          result.warn(
            `quest-theme-override: media query list with prefers-color-scheme left untouched: @media ${atRule.params}`,
            { node: atRule },
          );
          continue;
        }
        const other: Scheme = scheme === 'light' ? 'dark' : 'light';

        // Forced copy: scoped to the explicit data-theme, media clause removed.
        const forced = atRule.clone();
        scopeRules(forced, `[data-theme="${scheme}"]`);
        const remaining = stripSchemeClause(forced.params);
        let insert: ChildNode[];
        if (remaining) {
          forced.params = remaining;
          insert = [forced];
        } else {
          insert = forced.nodes ? [...forced.nodes] : [];
        }

        // Auto copy: original media query, suppressed by the opposite theme.
        scopeRules(atRule, `:not([data-theme="${other}"])`);
        atRule.after(insert);
      }
    },
  };
}
themeOverridePlugin.postcss = true;
