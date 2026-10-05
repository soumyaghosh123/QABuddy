import { describe, expect, it } from 'vitest';
import { parseSSE } from './sse';

describe('parseSSE', () => {
  it('parses several events delivered in one read', () => {
    const buffer = { rest: '' };
    const events = parseSSE('event: token\ndata: {"t":"Hel"}\n\nevent: token\ndata: {"t":"lo"}\n\n', buffer);
    expect(events).toEqual([
      { event: 'token', data: { t: 'Hel' } },
      { event: 'token', data: { t: 'lo' } },
    ]);
    expect(buffer.rest).toBe('');
  });

  it('keeps a partial event until the rest arrives', () => {
    const buffer = { rest: '' };
    expect(parseSSE('event: sour', buffer)).toEqual([]);
    expect(parseSSE('ces\ndata: [{"n":1}]\n', buffer)).toEqual([]);
    expect(parseSSE('\n', buffer)).toEqual([{ event: 'sources', data: [{ n: 1 }] }]);
  });

  it('accepts CRLF line endings', () => {
    const buffer = { rest: '' };
    expect(parseSSE('event: done\r\ndata: {"uncited":false}\r\n\r\n', buffer)).toEqual([
      { event: 'done', data: { uncited: false } },
    ]);
  });
});
