import { describe, expect, it } from 'vitest';
import { healthState, stackPills, statusLabel } from './components/StackBar';
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

const full: Health = {
  ...base,
  llm: { provider: 'deepseek', model: 'deepseek-flash', ok: true },
  stack: {
    vector_db: 'Qdrant',
    search: 'hybrid RRF',
    embed_model: 'qwen3-embedding:0.6b',
    rerank_model: 'Xenova/ms-marco-MiniLM-L-12-v2',
  },
};

describe('stackPills', () => {
  it('lists vector DB, embeddings, reranker and LLM', () => {
    const pills = stackPills(full, false);
    expect(pills.map((p) => [p.label, p.value, p.status])).toEqual([
      ['Vector DB', 'Qdrant · hybrid RRF', 'ok'],
      ['Embeddings', 'qwen3-embedding:0.6b', 'ok'],
      ['Reranker', 'ms-marco-MiniLM-L-12-v2 · cross-encoder', 'none'],
      ['LLM', 'DeepSeek · deepseek-flash', 'ok'],
    ]);
  });

  it('marks the component that is down', () => {
    const pills = stackPills({ ...full, qdrant: { ok: false, error: 'Qdrant not reachable: ConnectError' } }, false);
    expect(pills[0]).toMatchObject({ status: 'down', title: 'Qdrant not reachable: ConnectError' });
    expect(pills[1].status).toBe('ok');
  });

  it('keeps the last known stack, greyed, while the API is offline', () => {
    const pills = stackPills(full, true);
    expect(pills.every((p) => p.status === 'stale' && p.title.startsWith('Last known: '))).toBe(true);
    expect(statusLabel(full, true, 'down')).toBe('API offline');
  });

  it('shows only the status pill before the first health response', () => {
    expect(stackPills(null, true)).toEqual([]);
    expect(statusLabel(null, true, 'down')).toBe('API offline');
  });

  it('summarises the index when ready', () => {
    expect(statusLabel(full, false, 'ok')).toBe(`Ready · ${(1162).toLocaleString()} chunks`);
  });
});
