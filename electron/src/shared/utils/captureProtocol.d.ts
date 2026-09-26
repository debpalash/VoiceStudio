export function isSherpaModel(id: unknown): boolean;
export function classifySherpaFinal(message: {
  text?: string;
  final_kind?: string;
}): 'summary' | 'terminator' | 'utterance' | 'ignore';
export function sherpaSummaryTail(text: string, committed: string[]): string;
export function aggregateDeliveryKind(current: string | null, next: string | null): string | null;
export function computeTypeDelta(
  previous: string,
  next: string,
): { backspaces: number; text: string; noop: boolean };
export function parsePasteError(error: unknown): { kind: string; message: string };
