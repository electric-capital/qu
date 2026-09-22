
make a todo list of the following items and go through the process.

1. look at the todo file: $ARGUMENTS (if no file was specified, ask the user which todo file to use)
2. find the next section of unfinished work. do not work on more than one phase subsection at a time.
3. use a subagent to research the source tree and come up with a detailed plan. Start research with the ./docs folder. The subagent must write the plan to a file in ./devplans/ (create the directory if it doesn't exist). Name the file based on the task being worked on (e.g., devplans/phase1-feature-name.md). Show the plan file path to the user (no need to confirm).
4. use another subagent to implement the plan by reading the plan file written in step 3
5. give the user the opportunity to test the work
6. if user approves, help them commit the work as a diff
7. Mark todo items completed in the todo file.
8. ALWAYS use a subagent to update project documentation in ./docs folder to reflect the changes made in this phase. Read ./docs/STYLE_GUIDE.md first to understand documentation conventions. Update existing docs or create new ones as needed.
9. Commit the documentation changes.
10. do not ask to move on to the next task. the user will clear the context and start over with this command again.