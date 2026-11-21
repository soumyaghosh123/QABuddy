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
  matches: Match[];
}

export interface StackPill {
  key: string;
  label: string;
  value: string;
  ok: boolean;
  error?: string;
}

export interface Health {
  status: { level: 'ok' | 'warn' | 'down'; text: string };
  stack: StackPill[];
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

export interface Match {
  type: 'testcase' | 'story' | 'page' | 'scenario' | 'code' | 'other';
  key: string;
  name: string;
  where: string;
  status: string;
  url: string;
  n: number[];
}

export interface Stats {
  by_source: Record<string, number>;
  files: number;
  failed_files: { path: string; error: string }[];
  sources: SourceOption[];
  desktop: boolean;
}

export interface Inputs {
  github_repos: string[];
  zephyr_projects: string[];
  confluence_spaces: string[];
  updated_by: string;
  updated_at: number | string | null;
}

export interface Measurement {
  pages: number;
  attachments: number;
  indexable_attachments: number;
  attachment_mb: number;
  est_chunks: number;
  est_ram_mb: number;
  est_embed_calls: number;
}

export interface LastSync {
  finished_at: number;
  machine: string;
  pending: boolean;
  sources: Record<string, { ok: boolean; error?: string }>;
}

export interface TraceRow {
  story_key: string;
  story_title: string;
  testcase_key: string;
  testcase_name: string;
  status: string;
  automated: string;
  gap: string;
}

export interface TraceSummary {
  stories: number;
  stories_covered: number;
  coverage_pct: number;
  testcases: number;
  automated_pct: number;
  suggestions: number;
}

export interface TraceResult {
  rows: TraceRow[];
  summary: TraceSummary;
}

export interface TraceRequest {
  scope: 'story' | 'folder' | 'project';
  value: string;
  project?: string;
  include_suggested: boolean;
}
