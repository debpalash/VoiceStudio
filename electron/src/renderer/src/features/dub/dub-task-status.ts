import type { DubSession } from './dub-session';

function preparationStageKey(eventType: unknown) {
  const type = typeof eventType === 'string' ? eventType : '';
  if (type.startsWith('download')) return 'dub.prep_download';
  if (type.startsWith('extract')) return 'dub.prep_extract';
  if (type.startsWith('demucs')) return 'dub.prep_demucs';
  if (type.startsWith('scene')) return 'dub.prep_scene';
  if (type === 'cached') return 'dub.prep_cached';
  return 'dub.prep_preparing';
}

export function dubStageKey(session: DubSession, phase = session.phase): string {
  if (session.agentPass && phase === session.phase) return 'dub.agent_' + session.agentPass.stage;
  switch (phase) {
    case 'preparing':
      return preparationStageKey(session.event?.type);
    case 'transcribing':
      return 'dub_workflow.transcribing_audio';
    case 'translating':
      return 'dub.translating';
    case 'cleaning':
      return 'dub.clean_up';
    case 'mirroring':
      return 'dub.mirror_delivery';
    case 'generating':
      return 'dub_workflow.generating_dub';
    default:
      return 'common.loading';
  }
}

export function dubProgressPercent(session: DubSession): number | null {
  if (typeof session.event?.percent === 'number') return session.event.percent;
  const current = Number(session.event?.current);
  const total = Number(session.event?.total);
  return Number.isFinite(current) && Number.isFinite(total) && total > 0
    ? (current / total) * 100
    : null;
}
