"""Federal Register API instructions for the system prompt.

Federal Register reads are handled via the ``authed_get`` tool, which makes
GET requests directly to the public Federal Register API at
``https://www.federalregister.gov/api/v1/...``. The API is free and requires
NO authentication, so no connection or credential setup is needed.
"""

FEDERAL_REGISTER_API_BASE = "https://www.federalregister.gov/api/v1"


# ---------------------------------------------------------------------------
# Instruction text for the Federal Register API
# ---------------------------------------------------------------------------

def get_instructions(base_url: str) -> str:
    """Federal Register API documentation section (accessed via authed_get)."""
    return f"""## Federal Register API (via authed_get)

Search and read US Federal Register documents (rules, proposed rules, notices,
presidential documents), agencies, and public-inspection (pre-publication)
documents. The Federal Register API is free, public, and requires **NO
authentication**. Access it with `authed_get` using the full API URL.

**Base URL:** `{FEDERAL_REGISTER_API_BASE}`

**Key API Paths:**

| Path | Description |
|------|-------------|
| `/api/v1/documents.json` | Search published documents (also `.csv` for CSV export) |
| `/api/v1/documents/{{document_number}}.json` | Fetch a single document by its document number |
| `/api/v1/documents/{{doc1}},{{doc2}}.json` | Fetch multiple documents (comma-separated document numbers) |
| `/api/v1/documents/facets/{{facet}}` | Aggregate document counts faceted by `daily`, `weekly`, `monthly`, `quarterly`, `yearly`, `agency`, `topic`, `section`, `type`, `subtype` |
| `/api/v1/public-inspection-documents.json` | Search public-inspection (pre-publication) documents |
| `/api/v1/public-inspection-documents/current.json` | All documents currently on public inspection |
| `/api/v1/public-inspection-documents/{{document_number}}.json` | A single public-inspection document |
| `/api/v1/agencies` | List all agencies (large response -- prefer `output_file`) |
| `/api/v1/agencies/{{slug_or_id}}` | A single agency by slug or numeric id |
| `/api/v1/suggested_searches` | List curated/suggested searches |
| `/api/v1/suggested_searches/{{slug}}` | A single suggested search by slug |

**Documents-search query parameters:**
- `conditions%5Bterm%5D`: full-text search string (`conditions[term]`).
- `conditions%5Bpublication_date%5D%5Bgte%5D` / `%5Blte%5D` / `%5Bis%5D`: date filters, `YYYY-MM-DD` (`conditions[publication_date][gte|lte|is]`).
- `conditions%5Btype%5D%5B%5D`: repeatable document-type filter (`conditions[type][]`). **Case matters** -- use the exact title-case values the API returns: `Rule`, `Proposed Rule`, `Notice`, `Presidential Document` (lowercase/uppercase like `RULE` silently returns zero results).
- `conditions%5Bagencies%5D%5B%5D`: repeatable agency slug filter (`conditions[agencies][]`).
- `per_page`: results per page (1-1000). `page`: 1-based page number.
- `order`: `relevance` (default with a term), `newest`, `oldest`, `executive_order_number`.
- `fields%5B%5D`: repeatable field selector to trim the payload (`fields[]`). URL-encode the brackets as `%5B%5D`. Useful fields: `title`, `document_number`, `type`, `abstract`, `publication_date`, `html_url`, `pdf_url`, `agencies`, `agency_names`, `excerpts`.
- `format`: `json` (default for `.json`) or `csv`.

**Trim your responses (IMPORTANT):** The `authed_get` size gate is ~3 KB.
ALWAYS use `fields%5B%5D` + a small `per_page` to keep results small. For the
large `/agencies` list or any CSV export, pass `output_file` so the body is
written to the workspace instead of being returned inline.

**Example tool calls:**

```
# Search documents by term, trimmed fields, small page
tool_call(tool_name="authed_get", arguments={{"url": "{FEDERAL_REGISTER_API_BASE}/documents.json?conditions%5Bterm%5D=clean%20air&per_page=5&fields%5B%5D=title&fields%5B%5D=document_number&fields%5B%5D=publication_date&order=newest"}})

# Fetch a single document by its document number
tool_call(tool_name="authed_get", arguments={{"url": "{FEDERAL_REGISTER_API_BASE}/documents/2026-10606.json?fields%5B%5D=title&fields%5B%5D=abstract&fields%5B%5D=html_url"}})

# Date-bounded, type-filtered search (note title-case type value)
tool_call(tool_name="authed_get", arguments={{"url": "{FEDERAL_REGISTER_API_BASE}/documents.json?conditions%5Btype%5D%5B%5D=Rule&conditions%5Bpublication_date%5D%5Bgte%5D=2026-01-01&per_page=5&fields%5B%5D=title&fields%5B%5D=document_number"}})

# A single agency by slug
tool_call(tool_name="authed_get", arguments={{"url": "{FEDERAL_REGISTER_API_BASE}/agencies/environmental-protection-agency"}})

# Full agencies list -- large, so stream it to the workspace
tool_call(tool_name="authed_get", arguments={{"url": "{FEDERAL_REGISTER_API_BASE}/agencies", "output_file": "agencies.json"}})

# Documents currently on public inspection
tool_call(tool_name="authed_get", arguments={{"url": "{FEDERAL_REGISTER_API_BASE}/public-inspection-documents/current.json?fields%5B%5D=title&fields%5B%5D=document_number"}})
```

**Important Notes:**
- No authentication is required -- credentials are not needed for this API.
- Document numbers look like `2026-10606`.
- Agency identifiers are slugs (e.g. `environmental-protection-agency`) or numeric ids (e.g. `44`).
- The `.json` suffix is optional (`/documents` == `/documents.json`); CSV is available on the documents search via `.csv` or `format=csv`.
- Full conditions / parameter reference: `https://www.federalregister.gov/developers/documentation/api/v1`"""
