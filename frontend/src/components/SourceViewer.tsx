import { useEffect, useState } from 'react';
import { fetchChunk } from '../api';
import type { ChunkDetail, SourceHit } from '../types';
import { sourceLabel } from '../sourceMeta';

interface Props {
  hit: SourceHit;
  onClose: () => void;
}

export function SourceViewer({ hit, onClose }: Props) {
  const [detail, setDetail] = useState<ChunkDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setDetail(null);
    setError(null);
    fetchChunk(hit.chunk_id)
      .then((d) => alive && setDetail(d))
      .catch(() => alive && setError('Could not load this source.'));
    return () => {
      alive = false;
    };
  }, [hit.chunk_id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const firstLine = Number(detail?.location?.line_start ?? 0);
  const lines = detail?.text.split('\n') ?? [];
  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label={`Source ${hit.n}: ${hit.citation}`}>
      <div className="viewer__backdrop" onClick={onClose} />
      <section className="viewer__panel">
        <header className="viewer__head">
          <div>
            <p className="viewer__kicker">
              [{hit.n}] {sourceLabel(hit.source)} · {hit.kind.replace('_', ' ')}
            </p>
            <h2>{hit.citation}</h2>
            <p className="viewer__path">{hit.path}</p>
          </div>
          <button type="button" className="btn btn--ghost" onClick={onClose} aria-label="Close source">
            Close
          </button>
        </header>
        {error && <p className="notice notice--error">{error}</p>}
        {!detail && !error && <p className="muted">Loading…</p>}
        {detail && (
          <>
            <p className="viewer__header">{detail.header}</p>
            <pre className="viewer__text">
              {lines.map((line, i) => (
                <span className="viewer__line" key={i}>
                  {firstLine > 0 && <span className="viewer__ln">{firstLine + i}</span>}
                  <span>{line || ' '}</span>
                </span>
              ))}
            </pre>
          </>
        )}
      </section>
    </div>
  );
}
