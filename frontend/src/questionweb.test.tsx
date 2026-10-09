import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { QuestionWeb } from './components/QuestionWeb';

const questions = [
  'Why did testLoginPositiveVWO fail in Jenkins build 142?',
  'Which test cases cover the login page?',
  'What should a new QA engineer do in their first week?',
];

const spokes = (container: HTMLElement) => Array.from(container.querySelectorAll('.web__spoke'));
const hot = (container: HTMLElement) => spokes(container).map((s) => s.classList.contains('web__spoke--hot'));

afterEach(cleanup);

describe('QuestionWeb', () => {
  it('asks the question that was clicked', () => {
    const onPick = vi.fn();
    render(<QuestionWeb questions={questions} onPick={onPick} />);
    fireEvent.click(screen.getByRole('button', { name: /test cases cover/ }));
    expect(onPick).toHaveBeenCalledWith('Which test cases cover the login page?');
  });

  it('draws one spoke per question', () => {
    const { container } = render(<QuestionWeb questions={questions} onPick={() => undefined} />);
    expect(spokes(container)).toHaveLength(3);
  });

  it('hovering a question lights up only its spoke', () => {
    const { container } = render(<QuestionWeb questions={questions} onPick={() => undefined} />);
    fireEvent.mouseEnter(screen.getByRole('button', { name: /test cases cover/ }));
    expect(hot(container)).toEqual([false, true, false]);
    fireEvent.mouseLeave(screen.getByRole('button', { name: /test cases cover/ }));
    expect(hot(container)).toEqual([false, false, false]);
  });

  it('keyboard focus lights up the spoke too', () => {
    const { container } = render(<QuestionWeb questions={questions} onPick={() => undefined} />);
    fireEvent.focus(screen.getByRole('button', { name: /first week/ }));
    expect(hot(container)).toEqual([false, false, true]);
  });

  it('tags a question with its likely source and leaves others untagged', () => {
    render(<QuestionWeb questions={questions} onPick={() => undefined} />);
    expect(screen.getByRole('button', { name: /Jenkins build 142/ }).querySelector('.tag--jenkins')).not.toBeNull();
    expect(screen.getByRole('button', { name: /first week/ }).querySelector('.tag')).toBeNull();
  });
});
