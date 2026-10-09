import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';

const API: Record<string, unknown> = {
  '/api/stats': {
    by_source: {},
    files: 0,
    failed_files: [],
    sources: [
      { id: 'selenium', label: 'Selenium framework' },
      { id: 'jenkins', label: 'Jenkins' },
    ],
  },
  '/api/examples': ['Which test cases cover the login page?', 'Why did build 142 fail?'],
  '/api/health': {
    status: { level: 'ok', text: 'Ready' },
    stack: [],
    index: { chunks: 1162, last_ingest_at: null, index_version: 1 },
  },
};

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => new Response(JSON.stringify(API[url]), { status: url in API ? 200 : 404 })),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('App', () => {
  it('the empty screen shows the example questions and no sources panel', async () => {
    render(<App />);
    expect(await screen.findByRole('button', { name: /test cases cover/ })).toBeTruthy();
    expect(screen.queryByRole('complementary', { name: 'Sources' })).toBeNull();
  });

  it('picking a mode narrows what the next question searches', async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /CI \/ Jenkins/ }));
    expect(screen.getByRole('textbox', { name: 'Question' }).getAttribute('placeholder')).toMatch(/1 selected source/);
  });

  it('the menu button opens the sidebar drawer and the backdrop closes it', async () => {
    const { container } = render(<App />);
    const app = container.querySelector('.app')!;
    expect(app.classList.contains('app--drawer')).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Open menu' }));
    expect(app.classList.contains('app--drawer')).toBe(true);
    fireEvent.click(container.querySelector('.drawer-backdrop')!);
    expect(app.classList.contains('app--drawer')).toBe(false);
  });
});
