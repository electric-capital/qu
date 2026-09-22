/**
 * remark plugin that runs after remark-math and reverts inline `$...$` spans
 * that look like currency rather than math.
 *
 * remark-math treats any `$...$` pair as inline math, so "costs $5 and $10"
 * renders "5 and " as a formula and eats both dollar signs. Pandoc's rule
 * avoids this: the opening `$` must be followed by non-whitespace, the closing
 * `$` must be preceded by non-whitespace, and the closing `$` must not be
 * immediately followed by a digit. This plugin applies that rule after the
 * fact by turning offending `inlineMath` nodes back into plain text.
 *
 * Genuine math like `$\rightarrow$`, `$x^2$` or `$5x + 3$` is untouched.
 */

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  position?: { start: { offset?: number }; end: { offset?: number } };
}

function looksLikeCurrency(node: MdNode, next: MdNode | undefined, source: string | undefined): boolean {
  const start = node.position?.start.offset;
  const end = node.position?.end.offset;
  if (source !== undefined && start !== undefined && end !== undefined) {
    // Raw `$...$` slice: the parser trims one padding space from `value`, so
    // inspect the source text to see whitespace next to the delimiters.
    const raw = source.slice(start, end);
    const inner = raw.replace(/^\$+/, '').replace(/\$+$/, '');
    if (inner === '' || /^\s/.test(inner) || /\s$/.test(inner)) return true;
    if (/^\d/.test(source.slice(end, end + 1))) return true;
    return false;
  }
  const value = node.value ?? '';
  if (value === '' || /^\s/.test(value) || /\s$/.test(value)) return true;
  if (next && next.type === 'text' && /^\d/.test(next.value ?? '')) return true;
  return false;
}

function walk(parent: MdNode, source: string | undefined): void {
  const children = parent.children;
  if (!children) return;
  for (let i = 0; i < children.length; i++) {
    const node = children[i];
    if (node.type === 'inlineMath') {
      if (looksLikeCurrency(node, children[i + 1], source)) {
        const start = node.position?.start.offset;
        const end = node.position?.end.offset;
        const raw = source !== undefined && start !== undefined && end !== undefined
          ? source.slice(start, end)
          : `$${node.value ?? ''}$`;
        children[i] = { type: 'text', value: raw };
      }
      continue;
    }
    walk(node, source);
  }
}

export default function remarkMathCurrencyGuard() {
  return (tree: MdNode, file: { value?: unknown }) => {
    const source = typeof file.value === 'string' ? file.value : undefined;
    walk(tree, source);
  };
}
