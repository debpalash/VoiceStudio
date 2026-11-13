import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  HistoryIcon,
  PhoneCallIcon,
  PhoneIcon,
  PhoneIncomingIcon,
  PlusIcon,
  RefreshCwIcon,
  WorkflowIcon,
} from 'lucide-react';
import { WorkspaceHeader } from '@/components/app-shell/workspace-header';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { describeError } from '@/lib/api/client';
import {
  callsUnavailable,
  renderDisclosure,
  getCallSettings,
  getReadiness,
  listCalls,
  startCall,
  type CallRecord,
  type CallSettings,
} from '@/lib/api/calls';
import { useProfiles } from '@/hooks/use-profiles';
import { tr } from '@/lib/i18n-text';
import { cn } from '@/lib/utils';
import { CallsUnavailable, ReadinessBanner } from './call-parts';
import { CallHistory } from './call-history';
import { CallDetails, CallView } from './call-view';
import { callPhase } from './call-state';
import { InboundSettings } from './inbound-settings';
import { EMPTY_DRAFT, NewCallForm, type CallDraft, type StartRequest } from './new-call-form';
import { callVoices } from './voices';
import { CALLS_KEY, callKey, useLiveCall } from './use-live-call';

export type CallsLayout = 'three' | 'two' | 'one';

function subscribe(notify: () => void) {
  const queries = ['(min-width: 1440px)', '(min-width: 1024px)'].map((query) =>
    window.matchMedia(query),
  );
  for (const query of queries) query.addEventListener('change', notify);
  return () => {
    for (const query of queries) query.removeEventListener('change', notify);
  };
}

/** ≥1440px: history | call | form. 1024–1440px: history | tabbed main. Narrower: one column. */
export function useCallsLayout(): CallsLayout {
  return useSyncExternalStore(
    subscribe,
    () =>
      window.matchMedia('(min-width: 1440px)').matches
        ? 'three'
        : window.matchMedia('(min-width: 1024px)').matches
          ? 'two'
          : 'one',
    () => 'three',
  );
}

const noRetryWhenMissing = (count: number, error: unknown) => !callsUnavailable(error) && count < 1;

function Pane({
  title,
  icon: Icon,
  actions,
  className,
  children,
}: {
  title: string;
  icon: typeof PhoneIcon;
  actions?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      aria-label={title}
      className={cn(
        'flex min-h-0 min-w-0 flex-col bg-[color-mix(in_oklab,var(--muted)_10%,var(--background))]',
        className,
      )}
    >
      <header className="flex h-11 shrink-0 items-center gap-2 border-b border-border/50 px-4">
        <Icon aria-hidden="true" className="size-4 text-muted-foreground" />
        <h2 className="min-w-0 flex-1 truncate text-sm font-medium">{title}</h2>
        {actions}
      </header>
      {children}
    </section>
  );
}

function EmptyCall() {
  const { t } = useTranslation();
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
      <PhoneCallIcon aria-hidden="true" className="size-8 text-muted-foreground/60" />
      <p className="text-sm font-medium">{t('calls.no_call_title')}</p>
      <p className="max-w-sm text-sm text-muted-foreground">{t('calls.no_call_body')}</p>
    </div>
  );
}

export function CallsPage({ onCanvas, initialDraft }: { onCanvas?: () => void; initialDraft?: Pick<CallDraft, 'to' | 'brief'> }) {
  const { t } = useTranslation();
  const layout = useCallsLayout();
  const queryClient = useQueryClient();
  const readiness = useQuery({
    queryKey: [...CALLS_KEY, 'readiness'],
    queryFn: ({ signal }) => getReadiness(signal),
    retry: noRetryWhenMissing,
  });
  const settings = useQuery({
    queryKey: [...CALLS_KEY, 'settings'],
    queryFn: ({ signal }) => getCallSettings(signal),
    retry: noRetryWhenMissing,
  });
  const calls = useQuery({
    queryKey: [...CALLS_KEY, 'list'],
    queryFn: ({ signal }) => listCalls(50, signal),
    retry: noRetryWhenMissing,
    refetchInterval: (query) =>
      query.state.data?.some((call) => callPhase(call.status) !== 'ended' && !call.ended_at)
        ? 5000
        : false,
  });
  const profiles = useProfiles();
  const voices = callVoices(profiles.data);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [side, setSide] = useState<'new' | 'details'>('new');
  const [tab, setTab] = useState<'history' | 'new' | 'call'>('new');
  const [inboundOpen, setInboundOpen] = useState(false);
  const [draft, setDraft] = useState<CallDraft>(() => ({ ...EMPTY_DRAFT, ...initialDraft }));
  useEffect(() => {
    if (initialDraft) setDraft((current) => ({ ...current, ...initialDraft }));
  }, [initialDraft]);
  // Full numbers never come back from the server (it returns them masked), so
  // "Call again" can only prefill numbers dialled in this session.
  const dialled = useRef(new Map<string, { to: string; maxMinutes: number }>());
  const live = useLiveCall(selectedId);

  const unavailable = callsUnavailable(readiness.error) || callsUnavailable(calls.error);
  const ready = Boolean(readiness.data?.length) && readiness.data!.every((item) => item.ok);
  const effectiveDraft =
    draft.profileId && voices.some((voice) => voice.id === draft.profileId)
      ? draft
      : { ...draft, profileId: voices[0]?.id ?? '' };

  const start = useMutation({
    mutationFn: (request: StartRequest) => startCall(request),
    onSuccess: (call, request) => {
      dialled.current.set(call.id, { to: request.to, maxMinutes: request.max_minutes });
      queryClient.setQueryData<CallRecord[]>([...CALLS_KEY, 'list'], (old) => [
        call,
        ...(old ?? []).filter((item) => item.id !== call.id),
      ]);
      queryClient.setQueryData(callKey(call.id), call);
      setSelectedId(call.id);
      setSide('details');
      setTab('call');
      setDraft((current) => ({ ...current, to: '', brief: '' }));
    },
    onError: (error) => {
      if (callsUnavailable(error)) void readiness.refetch();
      toast.error(tr('calls.start_failed', { message: describeError(error) }));
    },
  });

  const select = (call: CallRecord) => {
    setSelectedId(call.id);
    setSide('details');
    setTab('call');
  };
  const newCall = () => {
    setSide('new');
    setTab('new');
  };
  const callAgain = (call: CallRecord) => {
    const previous = dialled.current.get(call.id);
    setDraft({
      ...draft,
      to: previous?.to ?? '',
      brief: call.brief,
      profileId: call.profile_id ?? draft.profileId,
      maxMinutes: previous?.maxMinutes ?? call.max_minutes ?? draft.maxMinutes,
    });
    newCall();
  };
  const recheck = () => {
    void readiness.refetch();
    void calls.refetch();
    void settings.refetch();
  };

  const selectedVoice =
    voices.find((voice) => voice.id === live.call?.profile_id) ??
    profiles.data?.find((profile) => profile.id === live.call?.profile_id) ??
    null;
  const maxMinutes =
    live.call?.max_minutes ??
    (selectedId ? dialled.current.get(selectedId)?.maxMinutes : null) ??
    null;
  const disclosureTemplate = renderDisclosure(
    settings.data?.disclosure_template || t('calls.disclosure_default'),
    settings.data?.user_name || voices.find((voice) => voice.id === effectiveDraft.profileId)?.name,
  );

  const form = (
    <div className="studio-scrollbar min-h-0 flex-1 overflow-y-auto px-4 py-4">
      <NewCallForm
        draft={effectiveDraft}
        onChange={setDraft}
        voices={voices}
        disclosureTemplate={disclosureTemplate}
        ready={ready}
        starting={start.isPending}
        onStart={(request) =>
          start.mutateAsync(request).then(
            () => undefined,
            () => undefined,
          )
        }
      />
    </div>
  );
  const details = <CallDetails live={live} voice={selectedVoice} onCallAgain={callAgain} />;
  const history = (
    <div className="studio-scrollbar min-h-0 flex-1 overflow-y-auto">
      <CallHistory
        calls={calls.data ?? []}
        loading={calls.isLoading}
        selectedId={selectedId}
        onSelect={select}
      />
    </div>
  );
  const historyActions = (
    <Button
      size="icon-xs"
      variant="ghost"
      aria-label={t('common.refresh')}
      title={t('common.refresh')}
      onClick={() => void calls.refetch()}
    >
      <RefreshCwIcon className={cn(calls.isFetching && 'animate-spin')} />
    </Button>
  );
  const callPane = (withDetails: boolean) =>
    selectedId ? (
      <CallView live={live} maxMinutes={maxMinutes} lead={withDetails ? details : undefined} />
    ) : (
      <EmptyCall />
    );

  let body: ReactNode;
  if (unavailable) {
    body = (
      <div className="studio-scrollbar min-h-0 flex-1 overflow-y-auto px-4">
        <CallsUnavailable onRetry={recheck} />
      </div>
    );
  } else if (layout === 'three') {
    body = (
      <div className="grid min-h-0 flex-1 grid-cols-[300px_minmax(0,1fr)_380px] min-[2560px]:grid-cols-[360px_minmax(0,1fr)_460px]">
        <Pane
          title={t('calls.history')}
          icon={HistoryIcon}
          actions={historyActions}
          className="border-r border-border/55"
        >
          {history}
        </Pane>
        <Pane title={t('calls.call')} icon={PhoneCallIcon} className="bg-background">
          {callPane(false)}
        </Pane>
        <Pane
          title={t(side === 'details' && selectedId ? 'calls.details' : 'calls.new_call')}
          icon={side === 'details' && selectedId ? PhoneIcon : PlusIcon}
          className="border-l border-border/55"
          actions={
            side === 'details' && selectedId ? (
              <Button size="xs" variant="outline" onClick={newCall}>
                <PlusIcon aria-hidden="true" />
                {t('calls.new_call')}
              </Button>
            ) : undefined
          }
        >
          {side === 'details' && selectedId ? (
            <div className="studio-scrollbar min-h-0 flex-1 overflow-y-auto px-4 py-4">
              {details}
            </div>
          ) : (
            form
          )}
        </Pane>
      </div>
    );
  } else {
    const tabs =
      layout === 'one' ? (['history', 'new', 'call'] as const) : (['new', 'call'] as const);
    const current = layout === 'two' && tab === 'history' ? 'new' : tab;
    const tabbed = (
      <Tabs
        value={current}
        onValueChange={(value) => setTab(value as typeof tab)}
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <div className="shrink-0 border-b border-border/50 px-4 py-2">
          <TabsList aria-label={t('calls.title')}>
            {tabs.map((value) => (
              <TabsTrigger key={value} value={value} className="px-3">
                {t(
                  value === 'history'
                    ? 'calls.history'
                    : value === 'new'
                      ? 'calls.new_call'
                      : 'calls.call',
                )}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        {layout === 'one' && (
          <TabsContent value="history" className="flex min-h-0 flex-col">
            {history}
          </TabsContent>
        )}
        <TabsContent value="new" className="flex min-h-0 flex-col">
          {form}
        </TabsContent>
        <TabsContent value="call" className="flex min-h-0 flex-col">
          {callPane(true)}
        </TabsContent>
      </Tabs>
    );
    body =
      layout === 'two' ? (
        <div className="grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)]">
          <Pane
            title={t('calls.history')}
            icon={HistoryIcon}
            actions={historyActions}
            className="border-r border-border/55"
          >
            {history}
          </Pane>
          <div className="flex min-h-0 min-w-0 flex-col">{tabbed}</div>
        </div>
      ) : (
        tabbed
      );
  }

  return (
    <div className="flex h-full min-h-0 flex-col" data-layout={layout}>
      <WorkspaceHeader>
        <h1 className="text-sm font-medium">{t('calls.title')}</h1>
        {onCanvas && <Button size="sm" variant="outline" onClick={onCanvas}><WorkflowIcon aria-hidden="true" />{t('workflows.canvas')}</Button>}
        <Button
          size="sm"
          variant="ghost"
          className="ml-2"
          disabled={unavailable}
          onClick={() => setInboundOpen(true)}
        >
          <PhoneIncomingIcon aria-hidden="true" />
          <span className="hidden sm:inline">{t('calls.inbound_title')}</span>
        </Button>
      </WorkspaceHeader>
      {!unavailable && readiness.data && (
        <div className="shrink-0 px-4 pt-3 empty:hidden">
          <ReadinessBanner
            items={readiness.data}
            onRecheck={recheck}
            checking={readiness.isFetching}
          />
        </div>
      )}
      {!unavailable && readiness.isError && (
        <p role="alert" className="mx-4 mt-3 flex items-center gap-2 text-sm text-destructive">
          {describeError(readiness.error)}
          <Button size="xs" variant="ghost" onClick={recheck}>
            {t('common.retry')}
          </Button>
        </p>
      )}
      <div
        className={cn(
          'flex min-h-0 flex-1 flex-col',
          !unavailable &&
            readiness.data?.some((item) => !item.ok) &&
            'mt-3 border-t border-border/50',
        )}
      >
        {body}
      </div>
      <InboundSettings
        open={inboundOpen}
        onOpenChange={setInboundOpen}
        settings={settings.data}
        onSaved={(next: CallSettings) => queryClient.setQueryData([...CALLS_KEY, 'settings'], next)}
      />
    </div>
  );
}
