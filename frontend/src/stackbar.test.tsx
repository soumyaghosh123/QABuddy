import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { StackBar } from './components/StackBar';
import type { Health } from './types';

const health: Health = {
  status: { level: 'ok', text: 'Ready · 1,162 chunks indexed · deepseek-flash' },
  stack: [
    { key: 'vector', label: 'Vector DB', value: 'Qdrant Cloud · hybrid RRF', ok: true },
    { key: 'embed', label: 'Embeddings', value: 'qwen3-embedding-0.6b · Cloudflare', ok: true },
    { key: 'rerank', label: 'Reranker', value: 'bge-reranker-base · Cloudflare', ok: true },
    { key: 'llm', label: 'LLM', value: 'DeepSeek · deepseek-flash', ok: true },
  ],
  index: { chunks: 1162, last_ingest_at: null, index_version: 1 },
};

const stackPills = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<HTMLElement>('.pill:not(.pill--status)'));

afterEach(cleanup);

describe('StackBar', () => {
  it('renders backend pills', () => {
    const { container } = render(<StackBar health={health} error={false} />);
    expect(stackPills(container)).toHaveLength(4);
    expect(screen.getByText('Qdrant Cloud · hybrid RRF')).toBeTruthy();
    expect(screen.getByText(/Ready · 1,162 chunks$/)).toBeTruthy();
  });

  it('marks a failing pill', () => {
    const failing: Health = {
      ...health,
      status: { level: 'warn', text: 'QDRANT_URL is not set' },
      stack: [{ ...health.stack[0], ok: false, error: 'QDRANT_URL is not set' }, ...health.stack.slice(1)],
    };
    const { container } = render(<StackBar health={failing} error={false} />);
    const vector = stackPills(container)[0];
    expect(vector.className).toContain('pill--down');
    expect(vector.title).toBe('QDRANT_URL is not set');
    expect(screen.getByText(/Degraded/)).toBeTruthy();
  });

  it('greys last-known pills when offline', () => {
    const { container } = render(<StackBar health={health} error={true} />);
    expect(screen.getByText(/API offline/)).toBeTruthy();
    const pills = stackPills(container);
    expect(pills).toHaveLength(4);
    for (const pill of pills) {
      expect(pill.className).toContain('pill--stale');
      expect(pill.title.startsWith('Last known: ')).toBe(true);
    }
  });

  it('shows only the status pill before first health', () => {
    const { container } = render(<StackBar health={null} error={true} />);
    expect(container.querySelectorAll('.pill')).toHaveLength(1);
    expect(screen.getByText(/API offline/)).toBeTruthy();
  });
});
