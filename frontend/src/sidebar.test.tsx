import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Sidebar } from './components/Sidebar';
import type { SourceOption } from './types';

const sources: SourceOption[] = [
  { id: 'selenium', label: 'Selenium framework' },
  { id: 'jira', label: 'JIRA' },
  { id: 'jenkins', label: 'Jenkins' },
];

const renderSidebar = (filters: string[] = []) => {
  const onFiltersChange = vi.fn();
  const onNewChat = vi.fn();
  render(<Sidebar sources={sources} filters={filters} onFiltersChange={onFiltersChange} onNewChat={onNewChat} />);
  return { onFiltersChange, onNewChat };
};

afterEach(cleanup);

describe('Sidebar', () => {
  it('picking a mode selects its sources', () => {
    const { onFiltersChange } = renderSidebar();
    fireEvent.click(screen.getByRole('button', { name: /CI \/ Jenkins/ }));
    expect(onFiltersChange).toHaveBeenCalledWith(['jenkins']);
  });

  it('with no filter, General QA is active and every source is ticked', () => {
    renderSidebar();
    expect(screen.getByRole('button', { name: /General QA/ }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByRole('button', { name: /Selenium/ }).getAttribute('aria-pressed')).toBe('false');
    for (const box of screen.getAllByRole('checkbox')) expect((box as HTMLInputElement).checked).toBe(true);
  });

  it('a filter shows only its sources as ticked', () => {
    renderSidebar(['jira']);
    expect((screen.getByRole('checkbox', { name: /JIRA/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole('checkbox', { name: /Jenkins/ }) as HTMLInputElement).checked).toBe(false);
    expect(screen.getByRole('button', { name: /Jira Tickets/ }).getAttribute('aria-pressed')).toBe('true');
  });

  it('unticking a source filters to the remaining ones', () => {
    const { onFiltersChange } = renderSidebar();
    fireEvent.click(screen.getByRole('checkbox', { name: /JIRA/ }));
    expect(onFiltersChange).toHaveBeenCalledWith(['selenium', 'jenkins']);
  });

  it('hides modes whose sources the backend does not have', () => {
    renderSidebar();
    expect(screen.queryByRole('button', { name: /Playwright/ })).toBeNull();
    expect(screen.getByRole('button', { name: /General QA/ })).toBeTruthy();
  });

  it('New chat starts over', () => {
    const { onNewChat } = renderSidebar();
    fireEvent.click(screen.getByRole('button', { name: /New chat/ }));
    expect(onNewChat).toHaveBeenCalled();
  });
});
