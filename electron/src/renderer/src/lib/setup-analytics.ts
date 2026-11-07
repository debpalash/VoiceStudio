/**
 * The analytics answer given before the backend exists. It only stands in until
 * the backend first answers: after that the backend is the authority for good,
 * so a later Settings change can never be undone by this copy.
 */
export const SETUP_ANALYTICS_KEY = 'voicestudio.setup.analytics-consent.v1';

export interface SetupAnalyticsChoice {
  enabled: boolean;
  /** The backend has answered, so this copy no longer decides anything. */
  settled: boolean;
}

export function readSetupAnalytics(): SetupAnalyticsChoice | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(SETUP_ANALYTICS_KEY) ?? 'null') as unknown;
    if (
      parsed &&
      typeof parsed === 'object' &&
      typeof (parsed as SetupAnalyticsChoice).enabled === 'boolean'
    ) {
      const { enabled, settled } = parsed as SetupAnalyticsChoice;
      return { enabled, settled: settled === true };
    }
  } catch {
    // Unreadable storage reads as "not asked". Silence is not consent.
  }
  return null;
}

export function rememberSetupAnalytics(choice: SetupAnalyticsChoice): void {
  try {
    localStorage.setItem(SETUP_ANALYTICS_KEY, JSON.stringify(choice));
  } catch {
    // Without storage the choice lasts this session, and the setup wizard asks later.
  }
}

/** Whether sending is allowed while no backend can say so. */
export function setupAnalyticsAllowed(
  choice: SetupAnalyticsChoice | null,
  killSwitched: boolean | undefined,
): boolean {
  return Boolean(choice?.enabled && !choice.settled && !killSwitched);
}

const BACKEND_FIRST_READY_KEY = 'voicestudio.setup.backend-first-ready.v1';

export interface FunnelStatus {
  stage: string;
  setupPhase?: string;
  failure?: { code: string };
}

export interface FunnelEvent {
  event: 'setup_started' | 'setup_failed' | 'setup_completed' | 'backend_first_ready';
  props: { stage?: string; error_class?: string };
}

/**
 * Turns status changes into funnel events. Only the stage and failure code ever
 * leave this function; everything else in the failure record stays on the machine.
 */
export class SetupFunnel {
  private previousStage: string | undefined;
  private reported = new Set<string>();
  private installing = false;

  observe(status: FunnelStatus, setupCompleted: boolean, canSend: boolean): FunnelEvent[] {
    const events: FunnelEvent[] = [];
    const previous = this.previousStage;
    this.previousStage = status.stage;
    if (setupCompleted) return events;
    if (status.stage === 'installing' && previous !== 'installing') {
      this.reported.clear();
      this.installing = true;
      events.push({ event: 'setup_started', props: { stage: status.setupPhase ?? 'checking' } });
    }
    const code = status.failure?.code;
    if (code && !this.reported.has(code)) {
      this.reported.add(code);
      events.push({ event: 'setup_failed', props: { stage: status.stage, error_class: code } });
    }
    // Cancel also leaves `installing` for `attaching`, so only a working backend proves it.
    if (status.stage === 'setup_required' || code) this.installing = false;
    if (status.stage === 'ready' && this.installing) {
      this.installing = false;
      events.push({ event: 'setup_completed', props: {} });
    }
    // Held until it can be sent: consent may only arrive after the first start.
    if (status.stage === 'ready' && canSend && !firstReadyRecorded()) {
      rememberFirstReady();
      events.push({ event: 'backend_first_ready', props: {} });
    }
    return events;
  }
}

function firstReadyRecorded(): boolean {
  try {
    return localStorage.getItem(BACKEND_FIRST_READY_KEY) === '1';
  } catch {
    return true;
  }
}

function rememberFirstReady(): void {
  try {
    localStorage.setItem(BACKEND_FIRST_READY_KEY, '1');
  } catch {
    // Reads as already recorded above, so it cannot repeat.
  }
}
