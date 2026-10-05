import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { linkCitations } from './citations';
import { Answer } from './components/Answer';

describe('linkCitations', () => {
  it('turns [n] markers into citation links', () => {
    expect(linkCitations('see [2] and [10].')).toBe('see [2](#cite-2) and [10](#cite-10).');
  });

  it('leaves code blocks and inline code alone', () => {
    const text = 'use `arr[0]` here [1]\n```java\nint x = a[1];\n```';
    expect(linkCitations(text)).toBe('use `arr[0]` here [1](#cite-1)\n```java\nint x = a[1];\n```');
  });
});

describe('Answer', () => {
  it('renders citation chips that call onCite with the source number', () => {
    const onCite = vi.fn();
    render(<Answer text="Root cause is the NAT IP [2]." onCite={onCite} />);
    fireEvent.click(screen.getByRole('button', { name: 'Source 2' }));
    expect(onCite).toHaveBeenCalledWith(2);
  });
});
