make a todo list of the following items and go through the process.

1. understand the feature request from the user's message

2. use a subagent to research the source tree and come up with a detailed plan. Start research with the ./docs folder. The subagent must write the plan to a file in ./devplans/ (writes to devplans/ are pre-allowed in settings, so this will not prompt). Show the plan file path to the user (no need to confirm). Save the agent ID returned by this subagent. Dont use the Plan subagent type for this, it needs to be able to write the plan file. IMPORTANT: instruct the subagent that it shouldn't write code in the plan, that is the coding agent's job. The planning agent's job is to gather as much relevant context as possible. When doing research, heavily prefer the docs and code as sources over existing devplans. Implemenations and designs may have deviated significantly compared to what is in a devplan.

3. if the user gives feedback on the plan, resume the planning subagent from step 2 (using its saved agent ID) and pass it the feedback. Instruct it to revise the existing plan file with the Edit tool (targeted edits, not a full rewrite). Repeat until the user is satisfied with the plan.

4. use another subagent to implement the plan by reading the plan file written in step 2.
   - Instruct this agent not to update docs outside of code comments, as that will happen in a later step.
   - Save the agent ID returned by this subagent.

5. give the user the opportunity to test the work. If the user reports issues or gives feedback on something that's not working:
   - resume the implementation subagent from step 4 (using its saved agent ID) and pass it the user's feedback. This preserves the agent's full context on all code it wrote.
   - repeat this step until the user is satisfied
   - IMPORTANT: always address user feedback through reinvoking the subagent to preserve your own context window.

6. if user approves, help them commit the work as a diff

7. ALWAYS use a subagent to update project documentation in ./docs folder to reflect the changes made.
   Read ./docs/STYLE_GUIDE.md first to understand documentation conventions. If you see parts of the documents that don't follow the guidelines,
   then revise them as you work. Update existing docs or create new ones as needed. Commit the documentation changes when finished.
