import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ChatPanel } from './components/ChatPanel';
import type { Turn } from './types';

const turn: Turn = {
  id: 7,
  question: 'Why does login fail on CI?',
  answer: 'The NAT IP is not allowlisted [1].',
  sources: [],
  streaming: false,
};

describe('ChatPanel', () => {
  it('clicking a citation selects the source without the turn click resetting it', () => {
    const onCite = vi.fn();
    const onSelectTurn = vi.fn();
    Element.prototype.scrollIntoView = vi.fn();
    render(<ChatPanel turns={[turn]} selectedTurn={7} activeCite={null} onCite={onCite} onSelectTurn={onSelectTurn} />);
    fireEvent.click(screen.getByRole('button', { name: 'Source 1' }));
    expect(onCite).toHaveBeenCalledWith(7, 1);
    expect(onSelectTurn).not.toHaveBeenCalled();
  });
});
