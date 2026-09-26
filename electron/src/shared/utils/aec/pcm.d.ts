export const AEC_NEAR: 0;
export const AEC_FAR: 1;
export function floatToInt16(frame: Float32Array): Int16Array<ArrayBuffer>;
export function tagFrame(
  frame: Int16Array | ArrayBuffer | ArrayLike<number>,
  kind: number,
): ArrayBuffer;
export function frameFromFloat(frame: Float32Array, kind: number): ArrayBuffer;
