import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchExamples, fetchHealth, fetchStats } from './api';
import { ChatPanel } from './components/ChatPanel';
import { EmptyState } from './components/EmptyState';
import { SourcesPanel } from './components/SourcesPanel';
import { SourceViewer } from './components/SourceViewer';
import { TopBar } from './components/TopBar';
import { streamChat } from './sse';
import type { Health, SourceHit, SourceOption, Turn } from './types';

const HISTORY_TURNS = 2;

export default function App() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [filters, setFilters] = useState<string[]>([]);
  const [sources, setSources] = useState<SourceOption[]>([]);
  const [examples, setExamples] = useState<string[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [selectedTurn, setSelectedTurn] = useState<number | null>(null);
  const [activeCite, setActiveCite] = useState<number | null>(null);
  const [viewer, setViewer] = useState<SourceHit | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const nextId = useRef(1);
  const streaming = turns.some((t) => t.streaming);

  useEffect(() => {
    fetchStats().then((s) => setSources(s.sources)).catch(() => undefined);
    fetchExamples().then(setExamples).catch(() => undefined);
    const poll = () =>
      fetchHealth()
        .then((h) => {
          setHealth(h);
          setHealthError(false);
        })
        .catch(() => setHealthError(true));
    poll();
    const timer = window.setInterval(poll, 15000);
    return () => window.clearInterval(timer);
  }, []);

  const update = (id: number, change: (turn: Turn) => Partial<Turn>) =>
    setTurns((all) => all.map((t) => (t.id === id ? { ...t, ...change(t) } : t)));

  const ask = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || streaming) return;
      const id = nextId.current++;
      const history = turns
        .filter((t) => t.answer && !t.streaming && !t.done?.no_answer)
        .slice(-HISTORY_TURNS)
        .flatMap((t) => [
          { role: 'user' as const, content: t.question },
          { role: 'assistant' as const, content: t.answer },
        ]);
      setTurns((all) => [...all, { id, question: text, answer: '', sources: [], streaming: true }]);
      setSelectedTurn(id);
      setActiveCite(null);
      setInput('');
      const controller = new AbortController();
      abortRef.current = controller;
      await streamChat(
        { question: text, sources: filters.length ? filters : undefined, history: history.length ? history : undefined },
        {
          onSources: (hits) => update(id, () => ({ sources: hits })),
          onToken: (t) => update(id, (turn) => ({ answer: turn.answer + t })),
          onDone: (done) => update(id, () => ({ done, streaming: false })),
          onError: (error) => update(id, () => ({ error })),
        },
        controller.signal,
      );
      update(id, () => ({ streaming: false }));
      abortRef.current = null;
    },
    [filters, streaming, turns],
  );

  const stop = () => abortRef.current?.abort();
  const newChat = () => {
    stop();
    setTurns([]);
    setSelectedTurn(null);
    setActiveCite(null);
    setViewer(null);
  };
  const toggleFilter = (id: string) =>
    setFilters((current) => (current.includes(id) ? current.filter((f) => f !== id) : [...current, id]));

  const current = turns.find((t) => t.id === selectedTurn) ?? turns[turns.length - 1] ?? null;

  const onCite = (turnId: number, n: number) => {
    setSelectedTurn(turnId);
    setActiveCite(n);
    const hit = turns.find((t) => t.id === turnId)?.sources.find((s) => s.n === n);
    if (hit) setViewer(hit);
  };

  return (
    <div className="app">
      <TopBar
        sources={sources}
        selected={filters}
        onToggle={toggleFilter}
        onClear={() => setFilters([])}
        health={health}
        healthError={healthError}
        onNewChat={newChat}
      />
      <main className="layout">
        <section className="chat" aria-label="Conversation">
          {turns.length === 0 ? (
            <EmptyState examples={examples} onPick={ask} />
          ) : (
            <ChatPanel
              turns={turns}
              selectedTurn={current?.id ?? null}
              activeCite={activeCite}
              onCite={onCite}
              onSelectTurn={(id) => {
                setSelectedTurn(id);
                setActiveCite(null);
              }}
            />
          )}
          <form
            className="composer"
            onSubmit={(e) => {
              e.preventDefault();
              void ask(input);
            }}
          >
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  void ask(input);
                }
              }}
              placeholder={
                filters.length ? `Ask about ${filters.length} selected source(s)…` : 'Ask about tests, failures, code, tickets…'
              }
              rows={2}
              maxLength={2000}
              aria-label="Question"
            />
            {streaming ? (
              <button type="button" className="btn btn--stop" onClick={stop}>
                Stop
              </button>
            ) : (
              <button type="submit" className="btn btn--primary" disabled={!input.trim()}>
                Ask
              </button>
            )}
          </form>
        </section>
        <aside className="sources" aria-label="Sources">
          <SourcesPanel turn={current} activeCite={activeCite} onOpen={(hit) => {
            setActiveCite(hit.n);
            setViewer(hit);
          }} />
        </aside>
      </main>
      {viewer && <SourceViewer hit={viewer} onClose={() => setViewer(null)} />}
    </div>
  );
}
