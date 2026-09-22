"""Airtable API instructions for the system prompt.

Airtable read operations are handled via the ``authed_get`` tool, which
makes authenticated GET requests directly to the Airtable API at
``https://api.airtable.com/v0/...``.
"""

AIRTABLE_API_BASE = "https://api.airtable.com/v0"


# ---------------------------------------------------------------------------
# Instruction text for the Airtable API
# ---------------------------------------------------------------------------

def get_instructions(base_url: str) -> str:
    """Airtable API documentation section (accessed via authed_get)."""
    return f"""## Airtable API (via authed_get)

Access the Airtable API using `authed_get` with the full Airtable API URL. Authentication is handled automatically.

**Base URL:** `{AIRTABLE_API_BASE}`

**Key API Paths:**

| Path | Description |
|------|-------------|
| `/v0/meta/bases` | List all accessible bases |
| `/v0/meta/bases/{{baseId}}/tables` | Get base schema (tables and fields) |
| `/v0/{{baseId}}/{{tableIdOrName}}` | List records from a table |
| `/v0/{{baseId}}/{{tableIdOrName}}/{{recordId}}` | Get a single record |
| `/v0/{{baseId}}/{{tableIdOrName}}/{{recordId}}/comments` | List comments on a record |

**List Records Parameters:**
- `view`: View ID or name to filter records
- `fields%5B%5D`: Field names to return (repeat for multiple fields, e.g., `fields%5B%5D=Name&fields%5B%5D=Status`). Note: Airtable uses `fields[]` array syntax, not comma-separated. URL-encode the brackets as `%5B%5D` for reliability.
- `filterByFormula`: Airtable formula to filter records (e.g., `{{{{Status}}}}='Active'`)
- `maxRecords`: Maximum total records to return
- `pageSize`: Records per page (max 100, default 100)
- `sort%5B0%5D%5Bfield%5D`: Sort field name (e.g., `sort%5B0%5D%5Bfield%5D=Name&sort%5B0%5D%5Bdirection%5D=asc`)
- `offset`: Pagination offset from previous response
- `cellFormat`: 'json' (default) or 'string'
- `timeZone`: IANA time zone string (e.g., 'America/Los_Angeles')
- `userLocale`: User locale string (e.g., 'en-us')

**List Comments Parameters:**
- `pageSize`: Comments per page (max 100)
- `offset`: Pagination offset from previous response

**Example tool calls:**

```
# List all bases
tool_call(tool_name="authed_get", arguments={{"url": "{AIRTABLE_API_BASE}/meta/bases"}})

# Get base schema (tables and fields)
tool_call(tool_name="authed_get", arguments={{"url": "{AIRTABLE_API_BASE}/meta/bases/appXXXXXXXXXXXXXX/tables"}})

# List all records from a table
tool_call(tool_name="authed_get", arguments={{"url": "{AIRTABLE_API_BASE}/appXXXXXXXXXXXXXX/tblYYYYYYYYYYYYYY"}})

# List records with specific fields (use fields%5B%5D= for each field)
tool_call(tool_name="authed_get", arguments={{"url": "{AIRTABLE_API_BASE}/appXXXXXXXXXXXXXX/tblYYYYYYYYYYYYYY?fields%5B%5D=Name&fields%5B%5D=Status"}})

# List records with filtering
tool_call(tool_name="authed_get", arguments={{"url": "{AIRTABLE_API_BASE}/appXXXXXXXXXXXXXX/Table%201?filterByFormula={{{{Status}}}}%3D'Active'"}})

# List records from a specific view
tool_call(tool_name="authed_get", arguments={{"url": "{AIRTABLE_API_BASE}/appXXXXXXXXXXXXXX/Table%201?view=Grid%20view"}})

# List records with sorting
tool_call(tool_name="authed_get", arguments={{"url": "{AIRTABLE_API_BASE}/appXXXXXXXXXXXXXX/tblYYYYYYYYYYYYYY?sort%5B0%5D%5Bfield%5D=Name&sort%5B0%5D%5Bdirection%5D=asc"}})

# Pagination (use offset from previous response)
tool_call(tool_name="authed_get", arguments={{"url": "{AIRTABLE_API_BASE}/appXXXXXXXXXXXXXX/tblYYYYYYYYYYYYYY?pageSize=50&offset=itrXXXXXXXXXXXXXX"}})

# Get a single record
tool_call(tool_name="authed_get", arguments={{"url": "{AIRTABLE_API_BASE}/appXXXXXXXXXXXXXX/tblYYYYYYYYYYYYYY/recZZZZZZZZZZZZZZ"}})

# List comments on a record
tool_call(tool_name="authed_get", arguments={{"url": "{AIRTABLE_API_BASE}/appXXXXXXXXXXXXXX/tblYYYYYYYYYYYYYY/recZZZZZZZZZZZZZZ/comments"}})

# Paginated comments
tool_call(tool_name="authed_get", arguments={{"url": "{AIRTABLE_API_BASE}/appXXXXXXXXXXXXXX/tblYYYYYYYYYYYYYY/recZZZZZZZZZZZZZZ/comments?pageSize=25"}})
```

**Important Notes:**
- Base IDs start with 'app' (e.g., appXXXXXXXXXXXXXX)
- Table IDs start with 'tbl' (e.g., tblYYYYYYYYYYYYYY) -- you can also use table names
- Record IDs start with 'rec' (e.g., recZZZZZZZZZZZZZZ)
- Rate limit: 5 requests per second, per base
- Maximum page size: 100 records
- Use `offset` from response for pagination (not page numbers)
- Airtable formula syntax: https://support.airtable.com/docs/formula-field-reference
- Table names with spaces must be URL-encoded (e.g., 'My Table' becomes 'My%20Table')
- Field selection uses `fields[]` array syntax: `fields%5B%5D=Name&fields%5B%5D=Status` (not comma-separated)
- Sort uses indexed array syntax: `sort%5B0%5D%5Bfield%5D=Name&sort%5B0%5D%5Bdirection%5D=asc`
- All parameter names use Airtable's native camelCase: `filterByFormula`, `maxRecords`, `pageSize`, `cellFormat`, `timeZone`, `userLocale`
- Personal Access Tokens require specific scopes -- ensure your token has read access to the bases/tables you need"""
