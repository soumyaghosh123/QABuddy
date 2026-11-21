import type { ChunkDetail, Health, Stats } from './types';

async function getJSON<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} → ${response.status}`);
  return (await response.json()) as T;
}

export type { Stats };
export const fetchHealth = () => getJSON<Health>('/api/health');
export const fetchStats = () => getJSON<Stats>('/api/stats');
export const fetchExamples = () => getJSON<string[]>('/api/examples');
export const fetchChunk = (id: string) => getJSON<ChunkDetail>(`/api/chunks/${encodeURIComponent(id)}`);
