import type { Health } from '../types';

type Level = Health['status']['level'];

const STATUS_ICON: Record<Level, string> = { ok: '✓', warn: '!', down: '✗' };

export function statusLabel(health: Health | null, error: boolean): string {
  if (error || !health) return 'API offline';
  if (health.status.level === 'ok') return `Ready · ${health.index.chunks.toLocaleString()} chunks`;
  return health.status.level === 'warn' ? 'Degraded' : 'Services down';
}

export function StackBar({ health, error }: { health: Health | null; error: boolean }) {
  const offline = error || !health;
  const level: Level = offline ? 'down' : health.status.level;
  return (
    <div className="stack" role="status" aria-label="Backend stack">
      <span className={`pill pill--status pill--${level}`} title={offline ? 'Server not reachable' : health.status.text}>
        <span aria-hidden="true">{STATUS_ICON[level]}</span> {statusLabel(health, error)}
      </span>
      {(health?.stack ?? []).map((pill) => {
        const state = offline ? 'stale' : pill.ok ? 'ok' : 'down';
        const detail = pill.error ?? pill.value;
        return (
          <span key={pill.key} className={`pill pill--${state}`} title={offline ? `Last known: ${detail}` : detail}>
            <span className="pill__dot" aria-hidden="true" />
            <span className="pill__label">{pill.label}</span>
            <span className="pill__value">{pill.value}</span>
          </span>
        );
      })}
    </div>
  );
}
