/**
 * API client for project database table browsing
 */

import { endpoints } from './config';
import type { ProjectTablesResponse, SortDirection, TableDataResponse, ApiError } from './types';

/**
 * Custom error class for project DB API errors
 */
export class ProjectDbApiError extends Error {
  statusCode?: number;
  errorCode?: string;

  constructor(message: string, statusCode?: number, errorCode?: string) {
    super(message);
    this.name = 'ProjectDbApiError';
    this.statusCode = statusCode;
    this.errorCode = errorCode;
  }
}

/**
 * Handle error responses from the API
 */
async function handleErrorResponse(response: Response): Promise<never> {
  let errorData: ApiError | null = null;

  try {
    errorData = await response.json();
  } catch {
    throw new ProjectDbApiError(
      response.statusText || 'Unknown error',
      response.status
    );
  }

  throw new ProjectDbApiError(
    errorData?.message || 'Unknown error',
    response.status,
    errorData?.error
  );
}

/**
 * Fetch the list of tables in a project's database
 */
export async function fetchProjectTables(projectId: string): Promise<ProjectTablesResponse> {
  const response = await fetch(endpoints.projectTables(projectId), {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
  });

  if (!response.ok) {
    await handleErrorResponse(response);
  }

  return await response.json();
}

/**
 * Fetch row data from a specific table in a project's database
 */
export async function fetchTableData(
  projectId: string,
  tableName: string,
  limit?: number,
  offset?: number,
  sortBy?: string | null,
  sortDir?: SortDirection
): Promise<TableDataResponse> {
  const url = new URL(`${window.location.origin}${endpoints.projectTableData(projectId, tableName)}`);
  if (limit !== undefined) {
    url.searchParams.set('limit', String(limit));
  }
  if (offset !== undefined) {
    url.searchParams.set('offset', String(offset));
  }
  if (sortBy) {
    url.searchParams.set('sort_by', sortBy);
    url.searchParams.set('sort_dir', sortDir ?? 'asc');
  }

  const response = await fetch(url.toString(), {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
  });

  if (!response.ok) {
    await handleErrorResponse(response);
  }

  return await response.json();
}

/**
 * Delete (drop) a table from a project's database
 */
export async function deleteProjectTable(
  projectId: string,
  tableName: string
): Promise<{ status: string; table_name: string }> {
  const response = await fetch(endpoints.projectTableData(projectId, tableName), {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
  });

  if (!response.ok) {
    await handleErrorResponse(response);
  }

  return await response.json();
}
