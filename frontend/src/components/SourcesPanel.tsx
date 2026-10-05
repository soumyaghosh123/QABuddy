import { useEffect, useRef } from 'react';
import type { SourceHit, Turn } from '../types';
import { sourceLabel, sourceShort } from '../sourceMeta';

interface Props {
  turn: Turn | null;
  activeCite: number | null;
  onOpen: (hit: SourceHit) => void;
}

function locationText(hit: SourceHit): string {
  const loc = hit.location ?? {};
  if (loc.line_start) return `lines ${loc.line_start}–${loc.line_end ?? loc.line_start}`;
  if (loc.page) return `page ${loc.page}`;
  if (loc.row) return `row ${loc.row}`;
  if (loc.time) return `at ${loc.time}`;
  if (loc.build) return `build #${loc.build}`;
  return '';
}

export function SourcesPanel({ turn, activeCite, onOpen }: Props) {
  const refs = useRef<Record<number, HTMLButtonElement | null>>({});
  useEffect(() => {
    if (activeCite != null) refs.current[activeCite]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [activeCite, turn?.id]);

  if (!turn) {
    return (
      <div className="sources__empty">
        <h2>Sources</h2>
        <p>Cited files, tickets, test cases and builds appear here with each answer.</p>
      </div>
    );
  }
  const cited = new Set(turn.done?.citations_used ?? []);
  return (
    <div className="sources__inner">
      <h2>
        Sources <span className="count">{turn.sources.length}</span>
      </h2>
      {turn.sources.length === 0 && !turn.streaming && <p className="muted">No matching sources.</p>}
      {turn.sources.length === 0 && turn.streaming && <p className="muted">Searching…</p>}
      <ol className="cards">
        {turn.sources.map((hit) => (
          <li key={hit.chunk_id}>
            <button
              type="button"
              ref={(el) => {
                refs.current[hit.n] = el;
              }}
              className={`card${activeCite === hit.n ? ' card--active' : ''}${cited.has(hit.n) ? ' card--cited' : ''}`}
              onClick={() => onOpen(hit)}
            >
              <span className="card__top">
                <span className="card__n">{hit.n}</span>
                <span className={`tag tag--${hit.source}`} title={sourceLabel(hit.source)}>
                  {sourceShort(hit.source)}
                </span>
                <span className="card__citation">{hit.citation}</span>
              </span>
              <span className="card__path" title={hit.path}>
                {hit.path}
                {locationText(hit) && ` · ${locationText(hit)}`}
              </span>
              <span className="card__snippet">{hit.snippet}</span>
              <span className="card__score">relevance {hit.score.toFixed(2)}</span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
