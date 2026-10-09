import { MODES, activeMode, applyMode, toggleSource } from '../filters';
import { sourceIcon } from '../sourceMeta';
import type { SourceOption } from '../types';

interface Props {
  sources: SourceOption[];
  filters: string[];
  onFiltersChange: (filters: string[]) => void;
  onNewChat: () => void;
}

export function Sidebar({ sources, filters, onFiltersChange, onNewChat }: Props) {
  const available = sources.map((s) => s.id);
  const current = activeMode(filters, available);
  // A mode whose sources are all missing would silently mean "everything", so hide it.
  const modes = MODES.filter((m) => !m.sources || m.sources.some((id) => available.includes(id)));

  return (
    <aside className="sidebar" aria-label="QABuddy navigation">
      <div className="sidebar__brand">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true">🧪</span>
          <span className="brand__name">QABuddy.ai</span>
        </div>
        <p className="brand__tagline">Hybrid RAG · Selenium · Playwright · Jira</p>
        <button type="button" className="btn btn--outline sidebar__new" onClick={onNewChat}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
            <path d="M8 3v10M3 8h10" />
          </svg>
          New chat
        </button>
      </div>
      <nav className="sidebar__section" aria-label="Mode">
        <h2 className="sidebar__label">Mode</h2>
        {modes.map((m) => (
          <button
            key={m.id}
            type="button"
            className={`mode${current === m.id ? ' mode--on' : ''}`}
            aria-pressed={current === m.id}
            onClick={() => onFiltersChange(applyMode(m.id, available))}
          >
            <span className="mode__icon" aria-hidden="true">{m.icon}</span>
            {m.label}
          </button>
        ))}
      </nav>
      <fieldset className="sidebar__section sidebar__sources">
        <legend className="sidebar__label">Knowledge sources</legend>
        {sources.map((s) => (
          <label key={s.id} className="source">
            <input
              type="checkbox"
              checked={filters.length === 0 || filters.includes(s.id)}
              onChange={() => onFiltersChange(toggleSource(filters, s.id, available))}
            />
            <span className="source__icon" aria-hidden="true">{sourceIcon(s.id)}</span>
            <span>{s.label}</span>
          </label>
        ))}
      </fieldset>
    </aside>
  );
}
