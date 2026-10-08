import type { SourceOption } from '../types';
import { sourceLabel } from '../sourceMeta';

interface Props {
  sources: SourceOption[];
  selected: string[];
  onToggle: (id: string) => void;
  onClear: () => void;
  onNewChat: () => void;
}

export function TopBar({ sources, selected, onToggle, onClear, onNewChat }: Props) {
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
        <button type="button" className="btn btn--ghost" onClick={onNewChat}>
          New chat
        </button>
      </div>
    </header>
  );
}
