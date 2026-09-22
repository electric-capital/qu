import React, { useMemo, useState } from 'react';
import type { SkillContentDiff, SkillContentDiffLine } from '../api/types';
import './SkillContentDiffPreview.css';

interface SkillContentDiffPreviewProps {
  diff: SkillContentDiff;
}

/** Unchanged lines kept around each change in the collapsed snippet view. */
const CONTEXT_LINES = 2;

interface Hunk {
  start: number; // inclusive index into diff.lines
  end: number;   // inclusive
}

/**
 * Group the visible-when-collapsed line indexes (changed lines plus
 * CONTEXT_LINES of context on each side) into contiguous hunks.
 */
function computeHunks(lines: SkillContentDiffLine[]): Hunk[] {
  const visible = new Set<number>();
  lines.forEach((line, i) => {
    if (line.type === 'context') return;
    const from = Math.max(0, i - CONTEXT_LINES);
    const to = Math.min(lines.length - 1, i + CONTEXT_LINES);
    for (let j = from; j <= to; j++) visible.add(j);
  });
  const hunks: Hunk[] = [];
  let current: Hunk | null = null;
  for (let i = 0; i < lines.length; i++) {
    if (!visible.has(i)) {
      current = null;
      continue;
    }
    if (current && current.end === i - 1) {
      current.end = i;
    } else {
      current = { start: i, end: i };
      hunks.push(current);
    }
  }
  return hunks;
}

/**
 * Renders the edit_skill approval card's content diff: a unified line diff
 * of the skill body (computed server-side at proposal time). Collapsed by
 * default to the changed hunks plus 2 context lines each side, with a
 * toggle to expand to the whole skill content (changed lines stay
 * highlighted).
 */
export function SkillContentDiffPreview({ diff }: SkillContentDiffPreviewProps) {
  const [showFull, setShowFull] = useState(false);
  const hunks = useMemo(() => computeHunks(diff.lines), [diff.lines]);
  if (diff.lines.length === 0) return null;

  const renderLine = (line: SkillContentDiffLine, i: number) => (
    <tr key={i} className={`skill-diff-line ${line.type}`}>
      <td className="skill-diff-gutter">{line.old_line ?? ''}</td>
      <td className="skill-diff-gutter">{line.new_line ?? ''}</td>
      <td className="skill-diff-marker">
        {line.type === 'add' ? '+' : line.type === 'del' ? '-' : ''}
      </td>
      <td className="skill-diff-text">{line.text || ' '}</td>
    </tr>
  );

  const hiddenBetween = (prevEnd: number, nextStart: number) => {
    const count = nextStart - prevEnd - 1;
    if (count <= 0) return null;
    return (
      <tr key={`sep-${prevEnd}`} className="skill-diff-separator">
        <td colSpan={4}>&#8943; {count} unchanged line{count === 1 ? '' : 's'} &#8943;</td>
      </tr>
    );
  };

  let rows: React.ReactNode[];
  if (showFull || hunks.length === 0) {
    rows = diff.lines.map(renderLine);
  } else {
    rows = [];
    let prevEnd = -1;
    for (const hunk of hunks) {
      const sep = hiddenBetween(prevEnd, hunk.start);
      if (sep) rows.push(sep);
      for (let i = hunk.start; i <= hunk.end; i++) {
        rows.push(renderLine(diff.lines[i], i));
      }
      prevEnd = hunk.end;
    }
    const tailSep = hiddenBetween(prevEnd, diff.lines.length);
    if (tailSep) rows.push(tailSep);
  }

  return (
    <div className="skill-diff">
      <div className="skill-diff-scroll">
        <table className="skill-diff-table">
          <tbody>{rows}</tbody>
        </table>
      </div>
      <div className="skill-diff-footer">
        <span className="skill-diff-stats">
          <span className="skill-diff-stat-add">+{diff.added}</span>
          {' / '}
          <span className="skill-diff-stat-del">-{diff.removed}</span>
          {' line(s)'}
        </span>
        <button
          type="button"
          className="skill-diff-toggle"
          onClick={() => setShowFull((prev) => !prev)}
        >
          {/* Generic label: this preview also renders routine prompt diffs,
              not just skill bodies. */}
          {showFull ? 'Show changes only' : 'Show full content'}
        </button>
      </div>
    </div>
  );
}
