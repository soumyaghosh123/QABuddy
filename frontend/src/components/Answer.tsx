import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import remarkGfm from 'remark-gfm';
import { linkCitations } from '../citations';
import type { Match } from '../types';

const MATCH_ICONS: Record<string, string> = {
  testcase: '🧪',
  story: '📘',
  page: '📄',
  scenario: '🥒',
  code: '⌨️',
  other: '•',
};

/** Desktop only: test cases, stories, pages and scenarios behind the answer. Hidden when empty. */
function Matches({ matches }: { matches: Match[] }) {
  if (matches.length === 0) return null;
  return (
    <section className="matches" aria-label="Matches">
      <h3 className="matches__title">Matches</h3>
      <table className="grid">
        <thead>
          <tr>
            <th>Type</th>
            <th>Key</th>
            <th>Name</th>
            <th>Where</th>
            <th>Status</th>
            <th>Open</th>
          </tr>
        </thead>
        <tbody>
          {matches.map((m, i) => (
            <tr key={`${m.type}-${m.key}-${i}`}>
              <td title={m.type}>
                <span aria-hidden="true">{MATCH_ICONS[m.type] ?? MATCH_ICONS.other}</span> {m.type}
              </td>
              <td className="grid__key">{m.key}</td>
              <td>{m.name}</td>
              <td>{m.where}</td>
              <td>{m.status}</td>
              <td>
                {m.url && (
                  <a href={m.url} target="_blank" rel="noreferrer">
                    Open
                  </a>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

interface Props {
  text: string;
  onCite: (n: number) => void;
  activeCite?: number | null;
  matches?: Match[];
}

export function Answer({ text, onCite, activeCite, matches = [] }: Props) {
  return (
    <div className="answer">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { detect: false, ignoreMissing: true }]]}
        components={{
          a({ href, children }) {
            const match = href?.match(/^#cite-(\d+)$/);
            if (match) {
              const n = Number(match[1]);
              return (
                <button
                  type="button"
                  className={`cite${activeCite === n ? ' cite--active' : ''}`}
                  aria-label={`Source ${n}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    onCite(n);
                  }}
                >
                  {n}
                </button>
              );
            }
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            );
          },
        }}
      >
        {linkCitations(text)}
      </ReactMarkdown>
      <Matches matches={matches} />
    </div>
  );
}
