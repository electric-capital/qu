/**
 * Extension-to-icon mapping for the file browser.
 * Maps file extensions to Lucide icon components and CSS color classes.
 */

import type { LucideIcon } from 'lucide-react';
import {
  File,
  FileCode,
  FileJson,
  FileText,
  FileImage,
  FileSpreadsheet,
  FileArchive,
  FileTerminal,
  Database,
  Presentation,
} from 'lucide-react';

interface FileIconInfo {
  Icon: LucideIcon;
  className: string;
}

const extensionMap = new Map<string, FileIconInfo>([
  // Python
  ['py', { Icon: FileCode, className: 'file-icon-python' }],
  ['pyw', { Icon: FileCode, className: 'file-icon-python' }],
  ['pyi', { Icon: FileCode, className: 'file-icon-python' }],

  // JavaScript
  ['js', { Icon: FileCode, className: 'file-icon-javascript' }],
  ['jsx', { Icon: FileCode, className: 'file-icon-javascript' }],
  ['mjs', { Icon: FileCode, className: 'file-icon-javascript' }],
  ['cjs', { Icon: FileCode, className: 'file-icon-javascript' }],

  // TypeScript
  ['ts', { Icon: FileCode, className: 'file-icon-typescript' }],
  ['tsx', { Icon: FileCode, className: 'file-icon-typescript' }],

  // HTML
  ['html', { Icon: FileCode, className: 'file-icon-html' }],
  ['htm', { Icon: FileCode, className: 'file-icon-html' }],

  // CSS
  ['css', { Icon: FileCode, className: 'file-icon-css' }],
  ['scss', { Icon: FileCode, className: 'file-icon-css' }],
  ['sass', { Icon: FileCode, className: 'file-icon-css' }],
  ['less', { Icon: FileCode, className: 'file-icon-css' }],

  // JSON
  ['json', { Icon: FileJson, className: 'file-icon-json' }],
  ['jsonl', { Icon: FileJson, className: 'file-icon-json' }],

  // Data/config
  ['yaml', { Icon: FileSpreadsheet, className: 'file-icon-data' }],
  ['yml', { Icon: FileSpreadsheet, className: 'file-icon-data' }],
  ['toml', { Icon: FileSpreadsheet, className: 'file-icon-data' }],
  ['xml', { Icon: FileSpreadsheet, className: 'file-icon-data' }],
  ['csv', { Icon: FileSpreadsheet, className: 'file-icon-data' }],

  // Markdown/text
  ['md', { Icon: FileText, className: 'file-icon-markdown' }],
  ['mdx', { Icon: FileText, className: 'file-icon-markdown' }],
  ['rst', { Icon: FileText, className: 'file-icon-markdown' }],
  ['txt', { Icon: FileText, className: 'file-icon-markdown' }],
  ['log', { Icon: FileText, className: 'file-icon-markdown' }],

  // Images
  ['png', { Icon: FileImage, className: 'file-icon-image' }],
  ['jpg', { Icon: FileImage, className: 'file-icon-image' }],
  ['jpeg', { Icon: FileImage, className: 'file-icon-image' }],
  ['gif', { Icon: FileImage, className: 'file-icon-image' }],
  ['svg', { Icon: FileImage, className: 'file-icon-image' }],
  ['webp', { Icon: FileImage, className: 'file-icon-image' }],
  ['bmp', { Icon: FileImage, className: 'file-icon-image' }],
  ['ico', { Icon: FileImage, className: 'file-icon-image' }],
  ['avif', { Icon: FileImage, className: 'file-icon-image' }],

  // PDF
  ['pdf', { Icon: FileText, className: 'file-icon-pdf' }],

  // Microsoft Office - Word
  ['doc', { Icon: FileText, className: 'file-icon-word' }],
  ['docx', { Icon: FileText, className: 'file-icon-word' }],

  // Microsoft Office - Excel
  ['xls', { Icon: FileSpreadsheet, className: 'file-icon-excel' }],
  ['xlsx', { Icon: FileSpreadsheet, className: 'file-icon-excel' }],

  // Microsoft Office - PowerPoint
  ['ppt', { Icon: Presentation, className: 'file-icon-powerpoint' }],
  ['pptx', { Icon: Presentation, className: 'file-icon-powerpoint' }],

  // Archives
  ['zip', { Icon: FileArchive, className: 'file-icon-archive' }],
  ['tar', { Icon: FileArchive, className: 'file-icon-archive' }],
  ['gz', { Icon: FileArchive, className: 'file-icon-archive' }],
  ['tgz', { Icon: FileArchive, className: 'file-icon-archive' }],
  ['bz2', { Icon: FileArchive, className: 'file-icon-archive' }],
  ['7z', { Icon: FileArchive, className: 'file-icon-archive' }],
  ['rar', { Icon: FileArchive, className: 'file-icon-archive' }],

  // Database
  ['db', { Icon: Database, className: 'file-icon-database' }],
  ['sqlite', { Icon: Database, className: 'file-icon-database' }],
  ['sqlite3', { Icon: Database, className: 'file-icon-database' }],
  ['sql', { Icon: Database, className: 'file-icon-database' }],

  // Shell
  ['sh', { Icon: FileTerminal, className: 'file-icon-shell' }],
  ['bash', { Icon: FileTerminal, className: 'file-icon-shell' }],
  ['zsh', { Icon: FileTerminal, className: 'file-icon-shell' }],
  ['fish', { Icon: FileTerminal, className: 'file-icon-shell' }],
  ['bat', { Icon: FileTerminal, className: 'file-icon-shell' }],
  ['cmd', { Icon: FileTerminal, className: 'file-icon-shell' }],
  ['ps1', { Icon: FileTerminal, className: 'file-icon-shell' }],
]);

const defaultIcon: FileIconInfo = { Icon: File, className: '' };

/**
 * Get the icon component and CSS class for a given filename.
 * Extension is extracted from the last dot in the filename, lowercased.
 */
export function getFileIconInfo(filename: string): FileIconInfo {
  const dotIndex = filename.lastIndexOf('.');
  if (dotIndex <= 0) return defaultIcon;
  const ext = filename.substring(dotIndex + 1).toLowerCase();
  return extensionMap.get(ext) ?? defaultIcon;
}
