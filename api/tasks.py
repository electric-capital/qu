"""Google Tasks API instructions for the system prompt.

Task read operations are handled via the ``authed_get`` tool, which
makes authenticated GET requests directly to the Google Tasks API at
``https://tasks.googleapis.com/tasks/v1/...``.
"""

TASKS_API_BASE = "https://tasks.googleapis.com/tasks/v1"


# ---------------------------------------------------------------------------
# Instruction text for the Tasks API
# ---------------------------------------------------------------------------

def get_instructions(base_url: str) -> str:
    """Tasks API documentation section (accessed via authed_get)."""
    return f"""## Google Tasks API (via authed_get)

Access the Google Tasks API using `authed_get` with the full Google Tasks API URL. Authentication is handled automatically.

**Base URL:** `{TASKS_API_BASE}`

**Key API Paths:**

| Path | Description |
|------|-------------|
| `/tasks/v1/users/@me/lists` | List all task lists |
| `/tasks/v1/users/@me/lists/{{tasklistId}}` | Get a specific task list |
| `/tasks/v1/lists/{{tasklistId}}/tasks` | List tasks in a task list |
| `/tasks/v1/lists/{{tasklistId}}/tasks/{{taskId}}` | Get a specific task |

**Task List Parameters (list task lists):**
- `maxResults`: Maximum number of task lists per page (default 20, max 100)
- `pageToken`: Token for pagination
- `fields`: Fields to include in response. Use to reduce response size and save tokens.

**Task Parameters (list tasks):**
- `maxResults`: Maximum number of tasks per page (default 20, max 100)
- `pageToken`: Token for pagination
- `completedMin`: Lower bound for task completion date (RFC3339 timestamp)
- `completedMax`: Upper bound for task completion date (RFC3339 timestamp)
- `dueMin`: Lower bound for task due date (RFC3339 timestamp)
- `dueMax`: Upper bound for task due date (RFC3339 timestamp)
- `showCompleted`: Include completed tasks (default true)
- `showDeleted`: Include deleted tasks (default false)
- `showHidden`: Include hidden tasks (default false)
- `updatedMin`: Lower bound for last modification time (RFC3339 timestamp)
- `fields`: Fields to include in response. Use to reduce response size and save tokens.

**Example tool calls:**

```
# List all task lists
tool_call(tool_name="authed_get", arguments={{"url": "{TASKS_API_BASE}/users/@me/lists"}})

# Get a specific task list
tool_call(tool_name="authed_get", arguments={{"url": "{TASKS_API_BASE}/users/@me/lists/TASKLIST_ID"}})

# List tasks in a task list
tool_call(tool_name="authed_get", arguments={{"url": "{TASKS_API_BASE}/lists/TASKLIST_ID/tasks?maxResults=50"}})

# List incomplete tasks only
tool_call(tool_name="authed_get", arguments={{"url": "{TASKS_API_BASE}/lists/TASKLIST_ID/tasks?showCompleted=false"}})

# List tasks with due dates in a range
tool_call(tool_name="authed_get", arguments={{"url": "{TASKS_API_BASE}/lists/TASKLIST_ID/tasks?dueMin=2026-01-01T00:00:00Z&dueMax=2026-02-01T00:00:00Z"}})

# Get a specific task
tool_call(tool_name="authed_get", arguments={{"url": "{TASKS_API_BASE}/lists/TASKLIST_ID/tasks/TASK_ID"}})

# List tasks with only title and due date (saves tokens)
tool_call(tool_name="authed_get", arguments={{"url": "{TASKS_API_BASE}/lists/TASKLIST_ID/tasks?fields=items(title,due,status)"}})
```

**Important Notes:**
- Task list IDs can be obtained from the list task lists endpoint
- Timestamps must be in RFC3339 format (e.g., '2026-01-01T00:00:00Z')
- Use the `fields` parameter to request only the data you need, which reduces response size and saves tokens. Example: `fields=items(title,due,status)` for task listings."""
