import { useEffect, useRef } from 'react';
import type { Turn } from '../types';
import { Answer } from './Answer';

interface Props {
  turns: Turn[];
  selectedTurn: number | null;
  activeCite: number | null;
  onCite: (turnId: number, n: number) => void;
  onSelectTurn: (turnId: number) => void;
}

const seconds = (ms?: number | null) => (ms == null ? '' : `${(ms / 1000).toFixed(1)} s`);

function Status({ turn }: { turn: Turn }) {
  if (!turn.streaming || turn.answer) return null;
  return (
    <p className="status">
      <span className="spinner" aria-hidden />
      {turn.sources.length ? `Reading ${turn.sources.length} sources and writing the answer…` : 'Searching the knowledge base…'}
    </p>
  );
}

function Notices({ turn }: { turn: Turn }) {
  const notices: { tone: string; text: string }[] = [];
  if (turn.error?.code === 'llm_unavailable')
    notices.push({ tone: 'warn', text: `LLM offline — showing matching sources only. ${turn.error.message}` });
  else if (turn.error?.code === 'index_unavailable')
    notices.push({ tone: 'error', text: `Knowledge base offline. ${turn.error.message}` });
  else if (turn.error) notices.push({ tone: 'error', text: turn.error.message });
  if (turn.done?.partial) notices.push({ tone: 'warn', text: 'Answer interrupted.' });
  if (turn.done?.uncited && turn.answer && !turn.done.no_answer)
    notices.push({ tone: 'warn', text: 'This answer has no citations — verify it against the sources.' });
  return (
    <>
      {notices.map((n) => (
        <p key={n.text} className={`notice notice--${n.tone}`}>
          {n.text}
        </p>
      ))}
    </>
  );
}

export function ChatPanel({ turns, selectedTurn, activeCite, onCite, onSelectTurn }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const last = turns[turns.length - 1];
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end' });
  }, [turns.length, last?.answer.length, last?.sources.length]);

  return (
    <div className="thread">
      {turns.map((turn) => (
        <article
          key={turn.id}
          className={`turn${selectedTurn === turn.id ? ' turn--selected' : ''}`}
          onClick={() => onSelectTurn(turn.id)}
        >
          <p className="question">{turn.question}</p>
          <div className="reply">
            <Status turn={turn} />
            {turn.answer && (
              <Answer
                text={turn.answer + (turn.streaming ? ' ▍' : '')}
                activeCite={selectedTurn === turn.id ? activeCite : null}
                onCite={(n) => onCite(turn.id, n)}
              />
            )}
            <Notices turn={turn} />
            {turn.done && (
              <p className="meta">
                {turn.done.cached && <span className="badge">cached</span>}
                {turn.done.no_answer && <span className="badge badge--muted">not found</span>}
                <span>
                  retrieval {seconds((turn.done.timings_ms.retrieve ?? 0) + (turn.done.timings_ms.rerank ?? 0))}
                  {turn.done.timings_ms.llm_first_token != null && ` · first word ${seconds(turn.done.timings_ms.llm_first_token)}`}
                  {` · total ${seconds(turn.done.timings_ms.total)}`}
                </span>
              </p>
            )}
          </div>
        </article>
      ))}
      <div ref={endRef} />
    </div>
  );
}
