/**
 * Collapsible JSON tree viewer with syntax coloring.
 *
 * Renders a parsed JSON value as an interactive tree where objects and arrays
 * can be expanded/collapsed. Designed for the FileViewerModal.
 */

import { useState, useCallback } from 'react';
import './JsonTreeViewer.css';

interface JsonTreeViewerProps {
  data: unknown;
}

/** Top-level wrapper that kicks off the recursive tree. */
export function JsonTreeViewer({ data }: JsonTreeViewerProps) {
  return (
    <div className="json-tree-viewer">
      <JsonNode value={data} depth={0} isLast={true} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Internal recursive node                                           */
/* ------------------------------------------------------------------ */

/** How many nesting levels are expanded by default. */
const DEFAULT_EXPAND_DEPTH = 2;

interface JsonNodeProps {
  /** The JSON value to render. */
  value: unknown;
  /** Current nesting depth (0 = root). */
  depth: number;
  /** Optional key label (for object entries). */
  keyName?: string;
  /** Whether this is the last entry in its parent (controls trailing comma). */
  isLast: boolean;
}

function JsonNode({ value, depth, keyName, isLast }: JsonNodeProps) {
  const [collapsed, setCollapsed] = useState(depth >= DEFAULT_EXPAND_DEPTH);

  const toggle = useCallback(() => setCollapsed((c) => !c), []);

  // Determine the type of the value
  if (value === null) {
    return (
      <div className="json-node json-node-primitive">
        {keyName !== undefined && <span className="json-key">{`"${keyName}"`}<span className="json-colon">: </span></span>}
        <span className="json-null">null</span>
        {!isLast && <span className="json-comma">,</span>}
      </div>
    );
  }

  if (Array.isArray(value)) {
    return (
      <ArrayNode
        value={value}
        depth={depth}
        keyName={keyName}
        isLast={isLast}
        collapsed={collapsed}
        onToggle={toggle}
      />
    );
  }

  if (typeof value === 'object') {
    return (
      <ObjectNode
        value={value as Record<string, unknown>}
        depth={depth}
        keyName={keyName}
        isLast={isLast}
        collapsed={collapsed}
        onToggle={toggle}
      />
    );
  }

  // Primitive values: string, number, boolean
  return (
    <div className="json-node json-node-primitive">
      {keyName !== undefined && <span className="json-key">{`"${keyName}"`}<span className="json-colon">: </span></span>}
      <PrimitiveValue value={value} />
      {!isLast && <span className="json-comma">,</span>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Primitive value renderer                                          */
/* ------------------------------------------------------------------ */

function PrimitiveValue({ value }: { value: unknown }) {
  if (typeof value === 'string') {
    return <span className="json-string">{`"${escapeString(value)}"`}</span>;
  }
  if (typeof value === 'number') {
    return <span className="json-number">{String(value)}</span>;
  }
  if (typeof value === 'boolean') {
    return <span className="json-boolean">{String(value)}</span>;
  }
  // Fallback (should not happen for valid JSON)
  return <span className="json-null">{String(value)}</span>;
}

/** Escape special characters for display in a JSON string literal. */
function escapeString(s: string): string {
  return s
    .replace(/\\/g, '\\\\')
    .replace(/"/g, '\\"')
    .replace(/\n/g, '\\n')
    .replace(/\r/g, '\\r')
    .replace(/\t/g, '\\t');
}

/* ------------------------------------------------------------------ */
/*  Object node                                                       */
/* ------------------------------------------------------------------ */

interface CompoundNodeProps {
  value: Record<string, unknown> | unknown[];
  depth: number;
  keyName?: string;
  isLast: boolean;
  collapsed: boolean;
  onToggle: () => void;
}

function ObjectNode({ value, depth, keyName, isLast, collapsed, onToggle }: CompoundNodeProps) {
  const obj = value as Record<string, unknown>;
  const keys = Object.keys(obj);
  const count = keys.length;

  return (
    <div className="json-node json-node-compound">
      <div className="json-node-header" onClick={onToggle}>
        <span className={`json-toggle ${collapsed ? 'json-toggle-collapsed' : 'json-toggle-expanded'}`} />
        {keyName !== undefined && <span className="json-key">{`"${keyName}"`}<span className="json-colon">: </span></span>}
        <span className="json-bracket">{'{'}</span>
        {collapsed && (
          <>
            <span className="json-badge">{count} {count === 1 ? 'key' : 'keys'}</span>
            <span className="json-bracket">{'}'}</span>
            {!isLast && <span className="json-comma">,</span>}
          </>
        )}
      </div>
      {!collapsed && (
        <>
          <div className="json-children">
            {keys.map((k, i) => (
              <JsonNode
                key={k}
                keyName={k}
                value={obj[k]}
                depth={depth + 1}
                isLast={i === keys.length - 1}
              />
            ))}
          </div>
          <div className="json-node-footer">
            <span className="json-bracket">{'}'}</span>
            {!isLast && <span className="json-comma">,</span>}
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Array node                                                        */
/* ------------------------------------------------------------------ */

function ArrayNode({ value, depth, keyName, isLast, collapsed, onToggle }: CompoundNodeProps) {
  const arr = value as unknown[];
  const count = arr.length;

  return (
    <div className="json-node json-node-compound">
      <div className="json-node-header" onClick={onToggle}>
        <span className={`json-toggle ${collapsed ? 'json-toggle-collapsed' : 'json-toggle-expanded'}`} />
        {keyName !== undefined && <span className="json-key">{`"${keyName}"`}<span className="json-colon">: </span></span>}
        <span className="json-bracket">{'['}</span>
        {collapsed && (
          <>
            <span className="json-badge">{count} {count === 1 ? 'item' : 'items'}</span>
            <span className="json-bracket">{']'}</span>
            {!isLast && <span className="json-comma">,</span>}
          </>
        )}
      </div>
      {!collapsed && (
        <>
          <div className="json-children">
            {arr.map((item, i) => (
              <JsonNode
                key={i}
                value={item}
                depth={depth + 1}
                isLast={i === arr.length - 1}
              />
            ))}
          </div>
          <div className="json-node-footer">
            <span className="json-bracket">{']'}</span>
            {!isLast && <span className="json-comma">,</span>}
          </div>
        </>
      )}
    </div>
  );
}
