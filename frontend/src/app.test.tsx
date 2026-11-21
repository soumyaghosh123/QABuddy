import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
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

  it('the web build makes no desktop requests and has no Settings entry', async () => {
    render(<App />);
    await screen.findByRole('button', { name: /test cases cover/ });
    expect(screen.queryByRole('button', { name: /Settings/ })).toBeNull();
    const urls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);
    expect(urls.filter((u) => u.startsWith('/api/desktop'))).toEqual([]);
  });

  const stubDesktop = (status: () => Response) =>
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url === '/api/stats') return new Response(JSON.stringify({ ...(API['/api/stats'] as object), desktop: true }));
        if (url === '/api/desktop/sync/status') return status();
        if (url === '/api/desktop/inputs')
          return new Response(JSON.stringify({ github_repos: [], zephyr_projects: [], confluence_spaces: [], updated_by: '', updated_at: null }));
        if (url === '/api/desktop/sync/last') return new Response('null');
        return new Response(JSON.stringify(API[url]), { status: url in API ? 200 : 404 });
      }),
    );

  const controlledStream = () => {
    const enc = new TextEncoder();
    let ctl!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start: (c) => (ctl = c) });
    return {
      response: () => new Response(body),
      send: (text: string) => ctl.enqueue(enc.encode(text)),
      close: () => ctl.close(),
    };
  };
  const PROGRESS = 'event: progress\ndata: {"line":"Zephyr"}\n\n';
  const FINISHED = 'event: finished\ndata: {"status":"done","pending":false,"error":""}\n\n';

  it('the startup sync banner shows while syncing and goes away on finished', async () => {
    const stream = controlledStream();
    stubDesktop(stream.response);
    render(<App />);
    await screen.findByRole('button', { name: /Settings/ });
    stream.send(PROGRESS);
    await screen.findByText('Syncing in the background…');
    stream.send(FINISHED);
    stream.close();
    await waitFor(() => expect(screen.queryByText('Syncing in the background…')).toBeNull());
  });

  it('the banner goes away when the stream closes without finished', async () => {
    const stream = controlledStream();
    stubDesktop(stream.response);
    render(<App />);
    await screen.findByRole('button', { name: /Settings/ });
    stream.send(PROGRESS);
    await screen.findByText('Syncing in the background…');
    stream.close();
    await waitFor(() => expect(screen.queryByText('Syncing in the background…')).toBeNull());
  });

  it('opening Settings removes the chat composer; New chat in the mobile bar returns to chat', async () => {
    stubDesktop(() => new Response('event: finished\ndata: {"status":"idle","pending":false,"error":""}\n\n'));
    const { container } = render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /Settings/ }));
    expect(await screen.findByRole('heading', { name: 'Settings' })).toBeTruthy();
    expect(screen.queryByRole('textbox', { name: 'Question', hidden: true })).toBeNull();
    fireEvent.click(container.querySelector('.mobilebar button[aria-label="New chat"]')!);
    expect(screen.getByRole('textbox', { name: 'Question' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Settings' })).toBeNull();
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
