// Desktop-only API client for /api/desktop/*. Reconstructed from the built bundle after the source was lost.
import { parseSSE } from './sse';
import type { SSEEvent } from './sse';
import type { Inputs, LastSync, Measurement, TraceRequest, TraceResult } from './types';

export class DesktopError extends Error {}

const UNREACHABLE = 'Cannot reach the QABuddy server. Is it running?';

async function errorText(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return body.message ?? body.detail?.message ?? `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new DesktopError(UNREACHABLE);
  }
  if (!response.ok) throw new DesktopError(await errorText(response));
  return (await response.json()) as T;
}

const postJson = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const fetchInputs = () => request<Inputs>('/api/desktop/inputs');
export const saveInputs = (inputs: Partial<Inputs>) =>
  request<Inputs>('/api/desktop/inputs', { ...postJson(inputs), method: 'PUT' });
export const startSync = () => request<{ status: string; holder?: string }>('/api/desktop/sync', { method: 'POST' });
export const fetchLastSync = () => request<LastSync | null>('/api/desktop/sync/last');
export const measureSpace = () => request<Measurement>('/api/desktop/measure', { method: 'POST' });
export const fetchTraceability = (req: TraceRequest) => request<TraceResult>('/api/desktop/traceability', postJson(req));

export interface SyncSourceEvent {
  id: string;
  ok: boolean;
  error?: string;
}

export interface SyncFinishedEvent {
  status: string;
  pending: boolean;
  error: string;
  holder: string;
}

export interface SyncHandlers {
  onProgress: (line: string) => void;
  onSource: (source: SyncSourceEvent) => void;
  onFinished: (result: SyncFinishedEvent) => void;
}

async function readEvents(body: ReadableStream<Uint8Array>, onEvent: (e: SSEEvent) => void): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const buffer = { rest: '' };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    for (const event of parseSSE(decoder.decode(value, { stream: true }), buffer)) onEvent(event);
  }
}

/** Follow the running sync (if any) until it finishes. Errors end the stream quietly. */
export async function streamSyncStatus(handlers: SyncHandlers, signal?: AbortSignal): Promise<void> {
  try {
    const response = await fetch('/api/desktop/sync/status', { signal });
    if (!response.ok || !response.body) return;
    await readEvents(response.body, ({ event, data }) => {
      if (event === 'progress') handlers.onProgress((data as { line: string }).line);
      else if (event === 'source') handlers.onSource(data as SyncSourceEvent);
      else if (event === 'finished') handlers.onFinished(data as SyncFinishedEvent);
    });
  } catch {
    /* aborted or connection lost */
  }
}

/** Download the traceability matrix as .xlsx using the server's file name. */
export async function downloadTraceability(req: TraceRequest): Promise<void> {
  let response: Response;
  try {
    response = await fetch('/api/desktop/traceability.xlsx', postJson(req));
  } catch {
    throw new DesktopError(UNREACHABLE);
  }
  if (!response.ok) throw new DesktopError(await errorText(response));
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? 'traceability.xlsx';
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export const errorMessage = (err: unknown) => (err instanceof DesktopError ? err.message : 'Something went wrong.');
