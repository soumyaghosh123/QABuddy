import type { Health, SourceOption } from '../types';
import { sourceLabel } from '../sourceMeta';

interface Props {
  sources: SourceOption[];
  selected: string[];
  onToggle: (id: string) => void;
  onClear: () => void;
  health: Health | null;
  healthError: boolean;
  onNewChat: () => void;
}

export function healthState(health: Health | null, error: boolean): { level: 'ok' | 'warn' | 'down'; text: string } {
  if (error || !health) return { level: 'down', text: 'Server not reachable' };
  const hosted = health.llm && health.llm.provider !== 'ollama' ? health.llm : null;
  const problems: string[] = [];
  if (!health.qdrant.ok) problems.push('Qdrant is down');
  if (!health.ollama.ok) problems.push('Ollama is down');
  else {
    if (!health.models.embed) problems.push('embedding model missing');
    if (!hosted && !health.models.llm) problems.push('LLM model missing');
  }
  if (hosted && !hosted.ok) problems.push(hosted.error ?? `${hosted.model} unavailable`);
  if (health.qdrant.ok && health.index.chunks === 0) problems.push('index is empty — run qabuddy ingest');
  if (problems.length === 0) {
    const model = hosted ? ` · ${hosted.model}` : '';
    return { level: 'ok', text: `Ready · ${health.index.chunks.toLocaleString()} chunks indexed${model}` };
  }
  return { level: health.qdrant.ok || health.ollama.ok ? 'warn' : 'down', text: problems.join(' · ') };
}

export function TopBar({ sources, selected, onToggle, onClear, health, healthError, onNewChat }: Props) {
  const state = healthState(health, healthError);
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand__mark">QA</span>
        <span className="brand__name">QABuddy</span>
      </div>
      <nav className="filters" aria-label="Filter by source">
        <button type="button" className={`chip${selected.length === 0 ? ' chip--on' : ''}`} onClick={onClear}>
          All
        </button>
        {sources.map((s) => (
          <button
            key={s.id}
            type="button"
            className={`chip${selected.includes(s.id) ? ' chip--on' : ''}`}
            aria-pressed={selected.includes(s.id)}
            onClick={() => onToggle(s.id)}
          >
            {sourceLabel(s.id)}
          </button>
        ))}
      </nav>
      <div className="topbar__right">
        <span className={`health health--${state.level}`} title={state.text} aria-label={state.text}>
          <span className="health__dot" />
          <span className="health__text">{state.level === 'ok' ? 'Ready' : state.level === 'warn' ? 'Degraded' : 'Offline'}</span>
        </span>
        <button type="button" className="btn btn--ghost" onClick={onNewChat}>
          New chat
        </button>
      </div>
    </header>
  );
}
