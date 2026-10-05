import ReactMarkdown from 'react-markdown';
import rehypeHighlight from 'rehype-highlight';
import remarkGfm from 'remark-gfm';
import { linkCitations } from '../citations';

interface Props {
  text: string;
  onCite: (n: number) => void;
  activeCite?: number | null;
}

export function Answer({ text, onCite, activeCite }: Props) {
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
    </div>
  );
}
