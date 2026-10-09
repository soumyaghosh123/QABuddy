/** Source filter rules. An empty filter means "search every source". */

export interface Mode {
  id: string;
  icon: string;
  label: string;
  sources: string[] | null; // null = every source
}

export const MODES: Mode[] = [
  { id: 'general', icon: '🔍', label: 'General QA', sources: null },
  { id: 'selenium', icon: '⌨️', label: 'Selenium', sources: ['selenium'] },
  { id: 'playwright', icon: '🎭', label: 'Playwright', sources: ['playwright'] },
  { id: 'jira', icon: '🐞', label: 'Jira Tickets', sources: ['jira'] },
  { id: 'jenkins', icon: '🤵', label: 'CI / Jenkins', sources: ['jenkins'] },
  { id: 'docs', icon: '📄', label: 'Docs & PRDs', sources: ['company_docs', 'requirements', 'meetings', 'lucid'] },
];

const normalize = (picked: string[], available: string[]) => {
  const kept = available.filter((id) => picked.includes(id));
  return kept.length === available.length ? [] : kept;
};

export function applyMode(modeId: string, available: string[]): string[] {
  const preset = MODES.find((m) => m.id === modeId)?.sources;
  return preset ? normalize(preset, available) : [];
}

export function toggleSource(filters: string[], id: string, available: string[]): string[] {
  const ticked = filters.length ? filters : available;
  if (ticked.includes(id)) {
    const rest = ticked.filter((s) => s !== id);
    return rest.length ? normalize(rest, available) : filters;
  }
  return normalize([...ticked, id], available);
}

export function activeMode(filters: string[], available: string[]): string | null {
  const sameSet = (a: string[], b: string[]) => a.length === b.length && a.every((x) => b.includes(x));
  return MODES.find((m) => sameSet(m.id === 'general' ? [] : applyMode(m.id, available), filters))?.id ?? null;
}
