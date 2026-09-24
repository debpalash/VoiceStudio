import { getBridge } from '@/components/bridge';
import { apiJson } from '@/lib/api/client';
import { decodeTextBytes } from '../../../../../frontend/src/utils/readTextFile';

export const SCRIPT_ACCEPT = '.txt,.md,.markdown,.doc,.docx,.pdf,.epub';

export async function importScript(file: File): Promise<string> {
  if (!file.size || file.size > 16 * 1024 * 1024) throw new Error('invalid_size');
  const ext = file.name.toLowerCase().split('.').pop();
  let text: string;
  if (ext === 'doc' || ext === 'docx') {
    const bridge = getBridge();
    if (!bridge?.files.extractScript) throw new Error('desktop_required');
    text = await bridge.files.extractScript({
      name: file.name,
      data: new Uint8Array(await file.arrayBuffer()),
    });
  } else if (ext === 'pdf' || ext === 'epub') {
    const form = new FormData();
    form.append('file', file);
    text = (await apiJson<{ text: string }>('/audiobook/import', { method: 'POST', body: form }))
      .text;
  } else if (ext === 'txt' || ext === 'md' || ext === 'markdown') {
    // Same decoder Stories and Dub paste use: a BOM names the encoding,
    // valid UTF-8 stays UTF-8, anything else is Windows-1252.
    text = decodeTextBytes(new Uint8Array(await file.arrayBuffer()));
  } else throw new Error('unsupported_file');
  if (!text.trim() || text.length > 1000000) throw new Error('invalid_text');
  return text.replace(/\r\n?/g, '\n');
}
