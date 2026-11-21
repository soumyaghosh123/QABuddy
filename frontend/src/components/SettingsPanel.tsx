// Placeholder: the desktop Settings panel source was lost and is being rebuilt. Shown only in the desktop app.
interface Props {
  indexedSources: number;
  onSynced: () => void;
}

export function SettingsPanel({ indexedSources }: Props) {
  return (
    <section className="panel" aria-label="Settings">
      <h2>Settings</h2>
      <p>This panel is being rebuilt. {indexedSources} sources are indexed.</p>
    </section>
  );
}
