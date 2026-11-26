export function splitSSEBuffer(
  buffer: string,
  afterCR?: boolean,
): { lines: string[]; rest: string; endsWithCR: boolean };
export function parseSSELine(line: string): unknown;
