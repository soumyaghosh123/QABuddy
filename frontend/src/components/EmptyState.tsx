import { QuestionWeb } from './QuestionWeb';

interface Props {
  examples: string[];
  onPick: (question: string) => void;
}

export function EmptyState({ examples, onPick }: Props) {
  return (
    <div className="empty">
      <h1 className="empty__title">
        <span aria-hidden="true">🧪</span> QABuddy.ai
      </h1>
      <p className="empty__lede">
        Ask anything grounded in your Selenium and Playwright frameworks, test cases, JIRA tickets, docs, meeting notes,
        requirements and Jenkins logs. Every answer cites its sources.
      </p>
      {examples.length > 0 && <QuestionWeb questions={examples} onPick={onPick} />}
    </div>
  );
}
