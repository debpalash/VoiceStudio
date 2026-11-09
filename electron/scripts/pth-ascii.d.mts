/** Rewrite a venv's non-ASCII `.pth` files so Python 3.11 can start (#1783). Returns the rewritten files. */
export function asciiSafePthFiles(venv: string): Promise<string[]>;
/** `asciiSafePthFiles` for synchronous callers. */
export function asciiSafePthFilesSync(venv: string): string[];
/** The ASCII-only equivalent of a `.pth` file's text. */
export function asciiSafePthText(text: string): string;
/** A single-quoted Python string literal using only printable ASCII. */
export function pythonLiteral(value: string): string;
