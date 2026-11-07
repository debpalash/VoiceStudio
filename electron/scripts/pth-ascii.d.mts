/** The file operations a heal uses; tests inject faults through it. */
export type PthFs = Pick<
  typeof import('node:fs/promises'),
  'chmod' | 'readdir' | 'readFile' | 'rename' | 'rm' | 'stat' | 'writeFile'
>;
export type PthFsSync = Pick<
  typeof import('node:fs'),
  'chmodSync' | 'readdirSync' | 'readFileSync' | 'renameSync' | 'rmSync' | 'statSync' | 'writeFileSync'
>;
export function asciiSafePthFiles(venv: string, fs?: PthFs): Promise<string[]>;
export function asciiSafePthFilesSync(venv: string, fs?: PthFsSync): string[];
export function asciiSafePthText(text: string): string;
export function pythonLiteral(value: string): string;
