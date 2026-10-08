export interface SourceHit {
  n: number;
  chunk_id: string;
  citation: string;
  source: string;
  kind: string;
  path: string;
  location: Record<string, string | number>;
  header: string;
  snippet: string;
  score: number;
}

export interface DoneInfo {
  citations_used: number[];
  uncited: boolean;
  no_answer: boolean;
  cached: boolean;
  partial?: boolean;
  timings_ms: { retrieve?: number; rerank?: number; llm_first_token?: number | null; total?: number };
}

export interface ChatError {
  code: 'llm_unavailable' | 'index_unavailable' | 'bad_request' | 'internal' | 'network' | string;
  message: string;
}

export interface ChatRequest {
  question: string;
  sources?: string[];
  history?: { role: 'user' | 'assistant'; content: string }[];
}

export interface Turn {
  id: number;
  question: string;
  answer: string;
  sources: SourceHit[];
  done?: DoneInfo;
  error?: ChatError;
  streaming: boolean;
}

export interface Health {
  qdrant: { ok: boolean; error?: string };
  ollama: { ok: boolean; error?: string };
  models: { embed: boolean; llm: boolean };
  llm?: { provider: string; model: string; ok: boolean; error?: string };
  stack?: { vector_db: string; search: string; embed_model: string; rerank_model: string };
  index: { chunks: number; last_ingest_at: number | null; index_version: number };
}

export interface SourceOption {
  id: string;
  label: string;
}

export interface ChunkDetail {
  chunk_id: string;
  citation: string;
  source: string;
  kind: string;
  path: string;
  location: Record<string, string | number>;
  title: string;
  breadcrumb: string[];
  header: string;
  text: string;
}
