export const SOURCE_META: Record<string, { short: string; label: string; icon: string }> = {
  selenium: { short: 'SE', label: 'Selenium', icon: '⌨️' },
  playwright: { short: 'PW', label: 'Playwright', icon: '🎭' },
  testcases: { short: 'TC', label: 'Test cases', icon: '🧪' },
  jira: { short: 'JIRA', label: 'JIRA', icon: '🐞' },
  company_docs: { short: 'DOC', label: 'Docs', icon: '📄' },
  meetings: { short: 'MTG', label: 'Meetings', icon: '📅' },
  lucid: { short: 'FLOW', label: 'Lucid', icon: '🔀' },
  requirements: { short: 'PRD', label: 'Requirements', icon: '📑' },
  jenkins: { short: 'CI', label: 'Jenkins', icon: '🤵' },
};

export const sourceShort = (id: string) => SOURCE_META[id]?.short ?? id.slice(0, 4).toUpperCase();
export const sourceLabel = (id: string) => SOURCE_META[id]?.label ?? id;
export const sourceIcon = (id: string) => SOURCE_META[id]?.icon ?? '📁';

// First match wins: a framework named in the question beats "build" or "pipeline".
const GUESSES: [RegExp, string][] = [
  [/\bplaywright\b/i, 'playwright'],
  [/\bselenium\b/i, 'selenium'],
  [/\b[A-Z][A-Z0-9]+-\d+\b/, 'jira'],
  [/\btest ?cases?\b/i, 'testcases'],
  [/\b(jenkins|build|pipeline|ci)\b/i, 'jenkins'],
  [/\b(meeting|decided)\b/i, 'meetings'],
  [/\b(prd|srs|brd|frd|requirements?)\b/i, 'requirements'],
];

/** Best-effort source tag for an example question, or null when nothing is obvious. */
export function guessSource(question: string): string | null {
  return GUESSES.find(([pattern]) => pattern.test(question))?.[1] ?? null;
}
