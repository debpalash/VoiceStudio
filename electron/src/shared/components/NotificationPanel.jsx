/**
 * NotificationPanel — bell icon in the header that opens the
 * Notifications tab in the footer status bar.
 */
import React from 'react';
import { Bell } from 'lucide-react';
import { useVisibleNotifications } from '../api/hooks';

export default function NotificationPanel() {
  // Shared TanStack Query cache entry with LogsFooter — one 30s poll,
  // minus the notes the user has dismissed (the badge must agree with
  // what the footer tab actually shows).
  const { notifications: notifs } = useVisibleNotifications();
  const count = notifs.length;
  const hasErrors = notifs.some((n) => n.level === 'error');
  const hasWarns = notifs.some((n) => n.level === 'warn');

  const openNotifications = () => {
    window.dispatchEvent(new CustomEvent('omni:open-notifications'));
  };

  return (
    <button
      className={`relative flex h-[28px] w-[28px] shrink-0 cursor-pointer items-center justify-center rounded-md border-0 bg-transparent p-0 transition-all duration-[0.15s] hover:bg-[rgba(255,255,255,0.04)] hover:text-fg ${count > 0 ? 'text-brand' : 'text-fg-muted'}`}
      onClick={openNotifications}
      aria-label={`Notifications (${count})`}
      title="Notifications"
    >
      <Bell size={14} className={count > 0 ? '-translate-x-0.5 translate-y-0.5' : ''} />
      {count > 0 && (
        <span
          className={`pointer-events-none absolute top-0 right-0 flex h-[14px] min-w-[14px] items-center justify-center rounded-[7px] px-[3px] font-mono text-[9px] font-bold leading-none text-white shadow-[0_1px_3px_rgba(0,0,0,0.4)] ${!hasErrors && hasWarns ? 'bg-warn' : 'bg-danger'}`}
        >
          {count}
        </span>
      )}
    </button>
  );
}
