import { describe, expect, it } from 'vitest';
import { guessSource, sourceIcon } from './sourceMeta';

describe('guessSource', () => {
  it.each([
    ['Why did testLoginPositiveVWO fail in Jenkins build 142?', 'jenkins'],
    ['Which test cases cover the login page?', 'testcases'],
    ['What is our coding standard for explicit waits in the Selenium framework?', 'selenium'],
    ['What is VWO-26 about and what is its status?', 'jira'],
    ['Why does the Playwright checkout test fail in build 88?', 'playwright'],
    ['What are the stages of the CI test pipeline in Jenkins?', 'jenkins'],
    ['What was decided in the login failure triage meeting?', 'meetings'],
  ])('%s → %s', (question, source) => {
    expect(guessSource(question)).toBe(source);
  });

  it('returns null when no source is obvious', () => {
    expect(guessSource('What should a new QA engineer do in their first week?')).toBeNull();
  });
});

describe('sourceIcon', () => {
  it('falls back to a folder for unknown sources', () => {
    expect(sourceIcon('figma')).toBe('📁');
  });
});
