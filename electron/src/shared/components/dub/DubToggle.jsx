export default function DubToggle({ label, title, checked, onChange, Icon }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={!!checked}
      aria-label={label}
      title={title}
      onClick={() => onChange(!checked)}
      className="dub-setting-toggle flex items-center justify-between gap-3 min-h-10 px-3 py-2 rounded-lg border-0 bg-[var(--chrome-hover-bg)] text-sm text-[var(--chrome-fg)] cursor-pointer hover:bg-[var(--chrome-accent-bg)] focus-visible:outline-2 focus-visible:outline-[var(--chrome-accent)]"
    >
      <span className="inline-flex items-center gap-2">
        {Icon && <Icon size={15} aria-hidden="true" />}
        {label}
      </span>
      <span
        aria-hidden="true"
        className={`relative shrink-0 w-8 h-[18px] rounded-full ${checked ? 'bg-[var(--chrome-accent)]' : 'bg-[var(--chrome-fg-dim)]'}`}
      >
        <span
          className={`absolute top-0.5 left-0.5 size-3.5 rounded-full bg-[var(--color-bg)] transition-transform motion-reduce:transition-none ${checked ? 'translate-x-3.5' : ''}`}
        />
      </span>
    </button>
  );
}
