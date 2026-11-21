import { Suspense, useEffect, useState } from 'react';
import { useStore } from '@tanstack/react-store';
import { retryableLazy } from '@/lib/lazy-boundary';
import { REPAIR_AGENT_OPEN_EVENT } from '@/lib/repair-agent-events';
import { agentConversation } from '@/lib/agent-conversation';
import { translationActivity } from '@/features/dub/translation-activity';

const VoiceStudioAgent = retryableLazy(
  () => import('./voice-studio-agent').then((m) => ({ default: m.VoiceStudioAgent })),
  null,
);

/** Lazy wrapper so the repair/assistant dock and its SVG icons only load when opened or active. */
export function RepairAgentDock() {
  const conversationActive = useStore(
    agentConversation,
    (state) => state.messages.length > 0 || Boolean(state.activeId),
  );
  const translationActive = useStore(translationActivity, (state) => state.runs.length > 0);
  const [mounted, setMounted] = useState(() => conversationActive || translationActive);

  useEffect(() => {
    if (conversationActive || translationActive) setMounted(true);
  }, [conversationActive, translationActive]);

  useEffect(() => {
    if (mounted) return;
    const onOpen = () => setMounted(true);
    window.addEventListener(REPAIR_AGENT_OPEN_EVENT, onOpen);
    return () => window.removeEventListener(REPAIR_AGENT_OPEN_EVENT, onOpen);
  }, [mounted]);

  if (!mounted) return null;
  return (
    <Suspense fallback={null}>
      <VoiceStudioAgent />
    </Suspense>
  );
}

