export const SOURCE_META: Record<string, { short: string; label: string }> = {
  selenium: { short: 'SE', label: 'Selenium' },
  playwright: { short: 'PW', label: 'Playwright' },
  testcases: { short: 'TC', label: 'Test cases' },
  jira: { short: 'JIRA', label: 'JIRA' },
  company_docs: { short: 'DOC', label: 'Docs' },
  meetings: { short: 'MTG', label: 'Meetings' },
  lucid: { short: 'FLOW', label: 'Lucid' },
  requirements: { short: 'PRD', label: 'Requirements' },
  jenkins: { short: 'CI', label: 'Jenkins' },
};

export const sourceShort = (id: string) => SOURCE_META[id]?.short ?? id.slice(0, 4).toUpperCase();
export const sourceLabel = (id: string) => SOURCE_META[id]?.label ?? id;
