import { Store } from '@tanstack/store';
import { useStore } from '@tanstack/react-store';
import type { ConvertResult } from '@/lib/api/convert';
type Conversion = {
  file: File | null;
  voice: string;
  search: string;
  match: boolean;
  keepDelivery: boolean;
  language: string;
  result: ConvertResult | null;
};
const empty = (): Conversion => ({
  file: null,
  voice: '',
  search: '',
  match: true,
  keepDelivery: false,
  language: 'Auto',
  result: null,
});
const conversion = new Store<Conversion>(empty());
export const useConversion = () => useStore(conversion);
export function setConversion<K extends keyof Conversion>(key: K, value: Conversion[K]) {
  conversion.setState((current) => ({ ...current, [key]: value }));
}
export const clearConversion = () => conversion.setState(empty);
