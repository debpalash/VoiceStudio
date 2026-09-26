import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
  SelectGroup,
  SelectLabel,
} from '../ui/select';

export default function VoiceSelect({
  id,
  label,
  value,
  onChange,
  options,
  groups = [],
  optionLabel = (option) => option.label ?? option,
  disabled = false,
  optionIcon,
}) {
  const items = (values) =>
    values.map((option) => {
      const key = typeof option === 'string' ? option : option.value;
      const Icon = optionIcon?.(key);
      return (
        <SelectItem
          key={key}
          value={key || '__default_input__'}
          className="min-h-10 cursor-pointer px-3 text-sm text-[var(--chrome-fg)] focus:bg-[var(--chrome-accent-bg)] focus:text-[var(--chrome-accent)] data-[state=checked]:text-[var(--chrome-accent)]"
        >
          <span className="inline-flex items-center gap-2">
            {Icon && <Icon size={16} aria-hidden="true" className="shrink-0 opacity-75" />}
            <span>{optionLabel(option)}</span>
          </span>
        </SelectItem>
      );
    });
  return (
    <Select
      value={value || '__default_input__'}
      onValueChange={(next) => onChange(next === '__default_input__' ? '' : next)}
      disabled={disabled}
    >
      <SelectTrigger
        id={id}
        aria-label={label}
        className="w-full min-h-12 border-transparent bg-[var(--chrome-hover-bg)] px-3 text-sm text-[var(--chrome-fg)] shadow-none hover:bg-[var(--chrome-accent-bg)]"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent
        collisionPadding={12}
        className="z-[150] max-h-80 bg-[var(--color-bg)] border-transparent rounded-lg shadow-xl"
      >
        {/* Restored profiles can contain a valid value outside today's curated
            choices. Keep it visible and selectable instead of showing blank. */}
        {value &&
          ![...options, ...groups.flatMap((group) => group.options)].some(
            (option) => (typeof option === 'string' ? option : option.value) === value,
          ) &&
          items([value])}
        {items(options)}
        {groups.map((group) => (
          <SelectGroup key={group.label}>
            <SelectLabel className="text-xs text-[var(--chrome-fg-muted)] px-3 pt-3">
              {group.label}
            </SelectLabel>
            {items(group.options)}
          </SelectGroup>
        ))}
      </SelectContent>
    </Select>
  );
}
