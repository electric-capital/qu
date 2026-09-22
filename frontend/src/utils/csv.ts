/**
 * Tiny self-contained, RFC-4180-aware CSV parser.
 *
 * Intentionally dependency-free: the no-egress build avoids adding npm packages
 * (see devplan 00084 npm-vulnerability churn), and the feature only needs to
 * parse comma-delimited `.csv` for client-side table preview.
 *
 * Handles:
 *  - quoted fields containing commas
 *  - quoted fields containing embedded newlines (\n / \r\n)
 *  - escaped double-quotes inside quoted fields ("")
 *  - mixed \n / \r\n / \r line endings
 *  - ragged rows (padded/truncated to header width by the caller via parseCsv)
 *
 * Out of scope: TSV / semicolon / other delimiters (comma only).
 */

export interface ParsedCsv {
  columns: string[];
  rows: string[][];
}

/**
 * Low-level tokenizer: splits raw CSV text into a 2D array of string fields.
 * Never throws. Returns [] for empty input.
 */
function tokenize(text: string): string[][] {
  const records: string[][] = [];
  let field = '';
  let record: string[] = [];
  let inQuotes = false;
  let recordHasContent = false;

  const pushField = () => {
    record.push(field);
    field = '';
  };
  const pushRecord = () => {
    pushField();
    records.push(record);
    record = [];
    recordHasContent = false;
  };

  const len = text.length;
  let i = 0;
  while (i < len) {
    const ch = text[i];

    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          // Escaped double-quote.
          field += '"';
          i += 2;
          continue;
        }
        inQuotes = false;
        i += 1;
        continue;
      }
      field += ch;
      i += 1;
      continue;
    }

    if (ch === '"') {
      inQuotes = true;
      recordHasContent = true;
      i += 1;
      continue;
    }

    if (ch === ',') {
      recordHasContent = true;
      pushField();
      i += 1;
      continue;
    }

    if (ch === '\r') {
      // Treat \r\n and lone \r as one record separator.
      pushRecord();
      if (text[i + 1] === '\n') {
        i += 2;
      } else {
        i += 1;
      }
      continue;
    }

    if (ch === '\n') {
      pushRecord();
      i += 1;
      continue;
    }

    recordHasContent = true;
    field += ch;
    i += 1;
  }

  // Flush trailing field/record. Skip a dangling empty record produced by a
  // trailing newline (recordHasContent === false and the buffered field is empty).
  if (recordHasContent || field.length > 0 || record.length > 0) {
    pushRecord();
  }

  return records;
}

/**
 * Parse raw CSV text into { columns, rows }.
 *
 * - First record is the header (columns).
 * - Data rows are normalized to the header width: short rows are padded with
 *   empty strings, extra cells are dropped, so the table grid stays rectangular.
 * - Never throws. On empty / header-less input returns empty columns and rows
 *   so the caller can fall back to the raw view.
 */
export function parseCsv(text: string): ParsedCsv {
  try {
    const records = tokenize(text);
    if (records.length === 0) {
      return { columns: [], rows: [] };
    }

    const columns = records[0];
    const width = columns.length;

    const rows: string[][] = [];
    for (let r = 1; r < records.length; r += 1) {
      const raw = records[r];
      // Drop a fully-empty trailing record (single empty cell from a final newline).
      if (raw.length === 1 && raw[0] === '') {
        continue;
      }
      const row = new Array<string>(width);
      for (let c = 0; c < width; c += 1) {
        row[c] = c < raw.length ? raw[c] : '';
      }
      rows.push(row);
    }

    return { columns, rows };
  } catch {
    // Defensive: any unexpected failure degrades to "no parse" so the modal
    // shows the raw source instead of crashing.
    return { columns: [], rows: [] };
  }
}
