import { describe, expect, it } from 'vitest';
import { healthState } from './components/TopBar';
import type { Health } from './types';

const base: Health = {
  qdrant: { ok: true },
  ollama: { ok: true },
  models: { embed: true, llm: true },
  index: { chunks: 1162, last_ingest_at: null, index_version: 1 },
};

describe('healthState', () => {
  it('names the hosted model when everything is ready', () => {
    const state = healthState({ ...base, llm: { provider: 'deepseek', model: 'deepseek-flash', ok: true } }, false);
    expect(state.level).toBe('ok');
    expect(state.text).toBe(`Ready · ${(1162).toLocaleString()} chunks indexed · deepseek-flash`);
  });

  it('shows the hosted LLM error instead of "LLM model missing"', () => {
    const health: Health = {
      ...base,
      models: { embed: true, llm: false },
      llm: { provider: 'deepseek', model: 'deepseek-flash', ok: false, error: 'DEEPSEEK_API_KEY is not set' },
    };
    const state = healthState(health, false);
    expect(state.level).toBe('warn');
    expect(state.text).toBe('DEEPSEEK_API_KEY is not set');
  });

  it('keeps the Ollama model check for the local provider', () => {
    const state = healthState({ ...base, models: { embed: true, llm: false } }, false);
    expect(state.text).toBe('LLM model missing');
  });
});
