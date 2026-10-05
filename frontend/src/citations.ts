const CITATION = /\[(\d+(?:\s*,\s*\d+)*)\](?!\()/g;

function linkPlain(text: string): string {
  return text.replace(CITATION, (_match, group: string) =>
    group
      .split(',')
      .map((n) => n.trim())
      .map((n) => `[${n}](#cite-${n})`)
      .join(' '),
  );
}

function linkOutsideInlineCode(text: string): string {
  return text
    .split(/(`[^`\n]*`)/)
    .map((part) => (part.startsWith('`') && part.endsWith('`') && part.length > 1 ? part : linkPlain(part)))
    .join('');
}

/** Turn [n] citation markers into Markdown links (#cite-n), leaving code blocks and inline code untouched. */
export function linkCitations(text: string): string {
  return text
    .split(/(```[\s\S]*?(?:```|$))/)
    .map((part) => (part.startsWith('```') ? part : linkOutsideInlineCode(part)))
    .join('');
}
