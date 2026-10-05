import type { ChatError, ChatRequest, DoneInfo, SourceHit } from './types';

export interface SSEEvent {
  event: string;
  data: unknown;
}

/** Parse Server-Sent Events from a stream chunk; incomplete events stay in `buffer.rest`. */
export function parseSSE(chunk: string, buffer: { rest: string }): SSEEvent[] {
  buffer.rest = (buffer.rest + chunk).replace(/\r\n/g, '\n');
  const blocks = buffer.rest.split('\n\n');
  buffer.rest = blocks.pop() ?? '';
  const events: SSEEvent[] = [];
  for (const block of blocks) {
    let event = 'message';
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
    }
    if (data.length === 0) continue;
    const raw = data.join('\n');
    let parsed: unknown = raw;
    try {
      parsed = JSON.parse(raw);
    } catch {
      /* keep raw text */
    }
    events.push({ event, data: parsed });
  }
  return events;
}

export interface ChatHandlers {
  onSources: (hits: SourceHit[]) => void;
  onToken: (text: string) => void;
  onDone: (info: DoneInfo) => void;
  onError: (error: ChatError) => void;
}

export async function streamChat(request: ChatRequest, handlers: ChatHandlers, signal?: AbortSignal): Promise<void> {
  let response: Response;
  try {
    response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === 'AbortError') return;
    handlers.onError({ code: 'network', message: 'Cannot reach the QABuddy server. Is it running?' });
    return;
  }
  if (!response.ok || !response.body) {
    let error: ChatError = { code: 'internal', message: `Request failed (${response.status})` };
    try {
      error = (await response.json()) as ChatError;
    } catch {
      /* not JSON */
    }
    handlers.onError(error);
    return;
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const buffer = { rest: '' };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const { event, data } of parseSSE(decoder.decode(value, { stream: true }), buffer)) {
        if (event === 'sources') handlers.onSources(data as SourceHit[]);
        else if (event === 'token') handlers.onToken((data as { t: string }).t);
        else if (event === 'done') handlers.onDone(data as DoneInfo);
        else if (event === 'error') handlers.onError(data as ChatError);
      }
    }
  } catch (err) {
    if ((err as Error).name !== 'AbortError') {
      handlers.onError({ code: 'network', message: 'The connection was interrupted.' });
    }
  }
}
