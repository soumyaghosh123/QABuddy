interface Props {
  examples: string[];
  onPick: (question: string) => void;
}

export function EmptyState({ examples, onPick }: Props) {
  return (
    <div className="empty">
      <h1>Ask QABuddy</h1>
      <p>
        Answers come from your Selenium and Playwright frameworks, test cases, JIRA, docs, meeting notes, Lucid charts,
        requirements and Jenkins results — every answer cites its sources.
      </p>
      {examples.length > 0 && (
        <div className="examples">
          {examples.map((q) => (
            <button key={q} type="button" className="example" onClick={() => onPick(q)}>
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
