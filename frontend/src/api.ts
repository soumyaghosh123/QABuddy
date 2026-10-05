import type { ChunkDetail, Health, SourceOption } from './types';

async function getJSON<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} → ${response.status}`);
  return (await response.json()) as T;
}

export interface Stats {
  by_source: Record<string, number>;
  files: number;
  failed_files: { path: string; error: string }[];
  sources: SourceOption[];
}

export const fetchHealth = () => getJSON<Health>('/api/health');
export const fetchStats = () => getJSON<Stats>('/api/stats');
export const fetchExamples = () => getJSON<string[]>('/api/examples');
export const fetchChunk = (id: string) => getJSON<ChunkDetail>(`/api/chunks/${encodeURIComponent(id)}`);
