/**
 * ToolCallGroup component for displaying grouped consecutive tool calls
 *
 * When the AI agent fires multiple consecutive tool calls without user messages
 * in between, this component groups them into a collapsible unit:
 * - Collapsed state: Shows only the latest call + "and N more tools" indicator
 * - Expanded state: Shows each call as its own entry, each further expandable
 */

import { useState } from 'react';
import type { ToolUseMessage as ToolUseMessageType, ToolResultMessage as ToolResultMessageType, SubAgentToolCallInfo, SubAgentFinishedInfo } from '../api/types';
import { ToolUseMessage } from './ToolUseMessage';
import './ToolCallGroup.css';

export interface ToolCallGroupItem {
  toolUse: ToolUseMessageType;
  toolResult?: ToolResultMessageType;
}

interface ToolCallGroupProps {
  items: ToolCallGroupItem[];
  subAgentToolCalls?: Map<string, SubAgentToolCallInfo[]>;
  subAgentReturned?: Map<string, Map<string, SubAgentFinishedInfo>>;
}

export function ToolCallGroup({ items, subAgentToolCalls, subAgentReturned }: ToolCallGroupProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  // Helper to look up child tool calls for a given tool_id
  const getChildToolCalls = (toolId: string): SubAgentToolCallInfo[] | undefined => {
    if (!subAgentToolCalls) return undefined;
    return subAgentToolCalls.get(toolId);
  };

  // Helper to look up per-agent finished state for a given parent tool_id
  const getReturnedForTool = (toolId: string): Map<string, SubAgentFinishedInfo> | undefined => {
    if (!subAgentReturned) return undefined;
    return subAgentReturned.get(toolId);
  };

  // If only 1 item, render it directly without group chrome
  if (items.length === 1) {
    return (
      <ToolUseMessage
        toolUse={items[0].toolUse}
        toolResult={items[0].toolResult}
        childToolCalls={getChildToolCalls(items[0].toolUse.tool_id)}
        subAgentReturned={getReturnedForTool(items[0].toolUse.tool_id)}
      />
    );
  }

  const lastItem = items[items.length - 1];
  const hiddenCount = items.length - 1;
  const allCompleted = items.every(item => item.toolResult != null);
  const runningCount = items.filter(item => !item.toolResult).length;

  return (
    <div className={`tool-call-group ${isExpanded ? 'expanded' : 'collapsed'}`}>
      {isExpanded ? (
        <>
          {/* Group header with collapse control */}
          <div className="tool-group-header" onClick={() => setIsExpanded(false)}>
            <div className="tool-group-header-icon">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2">
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </div>
            <span className="tool-group-header-label">
              {items.length} tool calls
            </span>
            {allCompleted && (
              <span className="tool-group-status completed">all completed</span>
            )}
            {!allCompleted && runningCount > 0 && (
              <span className="tool-group-status running">
                {runningCount} running...
              </span>
            )}
          </div>
          {/* All tool calls rendered individually */}
          <div className="tool-group-items">
            {items.map((item, idx) => (
              <ToolUseMessage
                key={item.toolUse.tool_id || `group-item-${idx}`}
                toolUse={item.toolUse}
                toolResult={item.toolResult}
                childToolCalls={getChildToolCalls(item.toolUse.tool_id)}
                subAgentReturned={getReturnedForTool(item.toolUse.tool_id)}
              />
            ))}
          </div>
        </>
      ) : (
        <>
          {/* Collapsed: show latest tool call + "and N more" indicator */}
          <ToolUseMessage
            toolUse={lastItem.toolUse}
            toolResult={lastItem.toolResult}
            childToolCalls={getChildToolCalls(lastItem.toolUse.tool_id)}
            subAgentReturned={getReturnedForTool(lastItem.toolUse.tool_id)}
          />
          <div
            className="tool-group-expand-bar"
            onClick={() => setIsExpanded(true)}
          >
            <span className="tool-group-expand-label">
              and {hiddenCount} more tool{hiddenCount !== 1 ? 's' : ''}
            </span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2"
                 className="tool-group-expand-chevron">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </div>
        </>
      )}
    </div>
  );
}
