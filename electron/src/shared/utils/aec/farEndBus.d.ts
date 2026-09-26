export function publishFarEnd(frame: Float32Array): void;
export function subscribeFarEnd(listener: (frame: Float32Array) => void): () => void;
export function farEndListenerCount(): number;
