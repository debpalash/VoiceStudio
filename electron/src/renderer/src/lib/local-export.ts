import { getBridge } from '@/components/bridge';
import { saveNativeData } from '@/lib/native-save';

export async function saveLocalFile(blob: Blob, suggestedName: string) {
  if (getBridge()) {
    const saved = await saveNativeData({
      data: new Uint8Array(await blob.arrayBuffer()),
      suggestedName,
    });
    if (saved) return saved;
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = suggestedName;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return { canceled: false };
}
