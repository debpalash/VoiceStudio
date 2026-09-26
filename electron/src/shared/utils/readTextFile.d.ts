export function decodeTextBytes(bytes: Uint8Array | ArrayBuffer): string;
export function readTextFile(file: Pick<Blob, 'arrayBuffer'>): Promise<string>;
