/**
 * JsonView: interactive JSON display for tool call inputs/outputs.
 *
 * Wraps the shared JsonTreeViewer (collapsible tree, first two key levels
 * expanded by default) in a tool-section box and adds a hover copy button
 * that copies the whole blob (pretty-printed) to the clipboard.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { JsonTreeViewer } from './JsonTreeViewer';
import './JsonView.css';

/**
 * Parse a raw tool output string into a JSON container if (and only if) it
 * looks like one. Scalars and non-JSON text return null so callers keep
 * their plain-text rendering.
 */
export function tryParseJsonContainer(text: string): Record<string, unknown> | unknown[] | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return null;
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed !== null && typeof parsed === 'object') {
      return parsed as Record<string, unknown> | unknown[];
    }
    return null;
  } catch {
    return null;
  }
}

export function JsonView({ value }: { value: unknown }) {
  const [copied, setCopied] = useState(false);
  const copyTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
    };
  }, []);

  const handleCopy = useCallback(() => {
    let text: string;
    try {
      text = JSON.stringify(value, null, 2);
    } catch {
      text = String(value);
    }
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
      copyTimeoutRef.current = window.setTimeout(() => setCopied(false), 2000);
    }).catch((err) => {
      console.error('Failed to copy JSON:', err);
    });
  }, [value]);

  return (
    <div className="json-view tool-section-content">
      <button
        className={`json-copy-btn${copied ? ' copied' : ''}`}
        onClick={handleCopy}
        title={copied ? 'Copied!' : 'Copy JSON to clipboard'}
      >
        {copied ? <Check size={13} /> : <Copy size={13} />}
      </button>
      <JsonTreeViewer data={value} />
    </div>
  );
}
