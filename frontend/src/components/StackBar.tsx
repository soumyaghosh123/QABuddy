import type { Health } from '../types';

type Level = 'ok' | 'warn' | 'down';

export function healthState(health: Health | null, error: boolean): { level: Level; text: string } {
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

export interface StackPill {
  key: string;
  label: string;
  value: string;
  // ok/down: checked by /api/health; stale: last known value while the API is offline; none: not health-checked
  status: 'ok' | 'down' | 'stale' | 'none';
  title: string;
}

const PROVIDERS: Record<string, string> = { deepseek: 'DeepSeek', ollama: 'Ollama' };

export function stackPills(health: Health | null, error: boolean): StackPill[] {
  const stack = health?.stack;
  if (!health || !stack) return [];
  const checked = (ok: boolean): StackPill['status'] => (error ? 'stale' : ok ? 'ok' : 'down');
  const embedOk = health.ollama.ok && health.models.embed;
  const pills: StackPill[] = [
    {
      key: 'vector',
      label: 'Vector DB',
      value: `${stack.vector_db} · ${stack.search}`,
      status: checked(health.qdrant.ok),
      title: health.qdrant.ok ? `${stack.vector_db}: dense + BM25 vectors fused with RRF` : (health.qdrant.error ?? 'Qdrant is down'),
    },
    {
      key: 'embed',
      label: 'Embeddings',
      value: stack.embed_model,
      status: checked(embedOk),
      title: !health.ollama.ok
        ? (health.ollama.error ?? 'Ollama is down')
        : embedOk ? `${stack.embed_model} via Ollama` : `Run: ollama pull ${stack.embed_model}`,
    },
    {
      key: 'rerank',
      label: 'Reranker',
      value: `${stack.rerank_model.split('/').pop()} · cross-encoder`,
      status: error ? 'stale' : 'none',
      title: `${stack.rerank_model} (runs locally inside the API)`,
    },
  ];
  if (health.llm) {
    const { provider, model, ok } = health.llm;
    pills.push({
      key: 'llm',
      label: 'LLM',
      value: `${PROVIDERS[provider] ?? provider} · ${model}`,
      status: checked(ok),
      title: ok ? `Answers are written by ${model}` : (health.llm.error ?? `${model} unavailable`),
    });
  }
  if (error) for (const pill of pills) pill.title = `Last known: ${pill.title}`;
  return pills;
}

const STATUS_ICON: Record<Level, string> = { ok: '✓', warn: '!', down: '✗' };

export function statusLabel(health: Health | null, error: boolean, level: Level): string {
  if (error || !health) return 'API offline';
  if (level === 'ok') return `Ready · ${health.index.chunks.toLocaleString()} chunks`;
  return level === 'warn' ? 'Degraded' : 'Services down';
}

export function StackBar({ health, error }: { health: Health | null; error: boolean }) {
  const state = healthState(health, error);
  const label = statusLabel(health, error, state.level);
  return (
    <div className="stack" role="status" aria-label="Backend stack">
      <span className={`pill pill--status pill--${state.level}`} title={state.text}>
        <span aria-hidden="true">{STATUS_ICON[state.level]}</span> {label}
      </span>
      {stackPills(health, error).map((pill) => (
        <span key={pill.key} className={`pill pill--${pill.status}`} title={pill.title}>
          {pill.status !== 'none' && <span className="pill__dot" aria-hidden="true" />}
          <span className="pill__label">{pill.label}</span>
          <span className="pill__value">{pill.value}</span>
        </span>
      ))}
    </div>
  );
}
