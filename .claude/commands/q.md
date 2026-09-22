Help the user explore a design question about the codebase.

1. Understand the design question from the user's message
2. Use a subagent to thoroughly research the codebase, starting with the ./docs folder. Look for:
   - Existing patterns and conventions
   - Related implementations
   - Architecture decisions already made
   - Constraints or requirements that might affect the answer
3. Present findings to the user with:
   - Summary of relevant existing code/patterns discovered
   - 2-3 potential approaches or answers, with tradeoffs for each
   - A recommended approach with reasoning
4. Answer any follow-up questions the user has
