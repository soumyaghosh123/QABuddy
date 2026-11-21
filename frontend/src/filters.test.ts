import { describe, expect, it } from 'vitest';
import { activeMode, applyMode, toggleSource } from './filters';

const ALL = ['selenium', 'playwright', 'testcases', 'jira', 'company_docs', 'meetings', 'lucid', 'requirements', 'jenkins'];

describe('applyMode', () => {
  it('General QA clears the filter so every source is searched', () => {
    expect(applyMode('general', ALL)).toEqual([]);
  });

  it('a mode selects its preset sources', () => {
    expect(applyMode('jenkins', ALL)).toEqual(['jenkins']);
  });

  it('keeps only preset sources the backend actually has, in backend order', () => {
    const available = ['meetings', 'requirements', 'company_docs', 'jenkins'];
    expect(applyMode('docs', available)).toEqual(['meetings', 'requirements', 'company_docs']);
  });

  it('falls back to every source when none of the preset is available', () => {
    expect(applyMode('selenium', ['jira', 'jenkins'])).toEqual([]);
  });
});

describe('prefix modes', () => {
  const DESKTOP = ['gh_ctx_web_automation', 'gh_ctx_api', 'zephyr_ausweb', 'zephyr_orion', 'confluence', 'jira_linked'];

  it('prefix modes resolve gh_ ids', () => {
    expect(applyMode('github', DESKTOP)).toEqual(['gh_ctx_web_automation', 'gh_ctx_api']);
    expect(applyMode('zephyr', DESKTOP)).toEqual(['zephyr_ausweb', 'zephyr_orion']);
    expect(applyMode('stories', DESKTOP)).toEqual(['confluence', 'jira_linked']);
    expect(activeMode(['gh_ctx_api', 'gh_ctx_web_automation'], DESKTOP)).toBe('github');
  });

  it('a prefix mode with no matching source falls back to every source', () => {
    expect(applyMode('github', ALL)).toEqual([]);
  });
});

describe('toggleSource', () => {
  it('unticking one source while all are ticked keeps the rest', () => {
    expect(toggleSource([], 'jira', ALL)).toEqual(ALL.filter((s) => s !== 'jira'));
  });

  it('ticking a source adds it in backend order', () => {
    expect(toggleSource(['jenkins'], 'jira', ALL)).toEqual(['jira', 'jenkins']);
  });

  it('ticking the last unticked source goes back to no filter', () => {
    expect(toggleSource(ALL.filter((s) => s !== 'lucid'), 'lucid', ALL)).toEqual([]);
  });

  it('refuses to untick the last ticked source', () => {
    expect(toggleSource(['selenium'], 'selenium', ALL)).toEqual(['selenium']);
  });
});

describe('activeMode', () => {
  it('no filter is General QA', () => {
    expect(activeMode([], ALL)).toBe('general');
  });

  it('a filter equal to a preset, in any order, is that mode', () => {
    expect(activeMode(['lucid', 'company_docs', 'meetings', 'requirements'], ALL)).toBe('docs');
  });

  it('a hand-picked mix matches no mode', () => {
    expect(activeMode(['jira', 'jenkins'], ALL)).toBeNull();
  });
});
