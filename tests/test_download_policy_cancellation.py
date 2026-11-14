"""Destination checks cannot outlive cancellation or turn into plain fallback."""
import asyncio
import socket
import threading

import httpx
import pytest


@pytest.fixture(autouse=True)
def default_private_policy(monkeypatch):
    monkeypatch.delenv('OMNIVOICE_ALLOW_PRIVATE_URL_IMPORTS',raising=False)


@pytest.mark.parametrize('phase',['head_redirect','get_single','get_range','pinned_dns'])
@pytest.mark.parametrize('exit_kind',['user_cancel','task_cancel','deadline'])
def test_policy_exit_returns_from_asyncio_run_before_stuck_dns_finishes(tmp_path,monkeypatch,phase,exit_kind):
    from core.url_safety import UnsafeURLError
    from services import segmented_download as sd
    entered=threading.Event();release=threading.Event();finished=threading.Event()
    outcome=[];seen=[];cancelled=threading.Event();controller=[]
    def check(url):
        entered.set()
        assert release.wait(3)
        raise UnsafeURLError('controlled late refusal')
    monkeypatch.setattr(sd,'check_public_url',check)
    real_resolve=socket.getaddrinfo
    def resolve(host,*args,**kwargs):
        if host=='pending-policy.example':
            check(host)
        return real_resolve(host,*args,**kwargs)
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    for name in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']:
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setenv('NO_PROXY','*')
    async def response(request):
        seen.append(request)
        if request.method=='HEAD' and phase!='head_redirect':
            return httpx.Response(200,headers={'Content-Length':'4','Accept-Ranges':'bytes' if phase=='get_range' else 'none'})
        return httpx.Response(302,headers={'Location':'https://pending-policy.example/final'})
    destination=tmp_path/'model'
    destination.write_bytes(b'previous complete destination')
    async def run():
        controller.extend([asyncio.get_running_loop(),asyncio.current_task()])
        budget=.1 if exit_kind=='deadline' else 2
        if phase=='pinned_dns':
            await sd.segmented_download('https://pending-policy.example/model',str(destination),cancel_check=cancelled.is_set,timeout=budget)
        else:
            async with httpx.AsyncClient(transport=httpx.MockTransport(response),timeout=budget) as client:
                await sd.segmented_download('https://origin.example/model',str(destination),client=client,cancel_check=cancelled.is_set)
    def caller():
        try:asyncio.run(run())
        except BaseException as error:outcome.append(error)
        finally:finished.set()
    thread=threading.Thread(target=caller)
    thread.start()
    try:
        assert entered.wait(2)
        if exit_kind=='user_cancel':cancelled.set()
        elif exit_kind=='task_cancel':controller[0].call_soon_threadsafe(controller[1].cancel)
        assert finished.wait(1), 'Policy exit waited for the blocked OS destination check'
        expected={'user_cancel':sd.DownloadCancelled,'task_cancel':asyncio.CancelledError,'deadline':UnsafeURLError}[exit_kind]
        assert len(outcome)==1 and isinstance(outcome[0],expected)
        assert destination.read_bytes()==b'previous complete destination'
    finally:
        release.set();thread.join(3)
    assert not thread.is_alive()


@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['head_redirect','get_single','get_range','pinned_dns'])
@pytest.mark.parametrize('ordinary_first',[False,True])
async def test_pending_policy_interruption_is_terminal_for_every_check_path(monkeypatch,phase,ordinary_first):
    from core.url_safety import UnsafeURLError
    from services import segmented_download as sd
    entered=threading.Event();release=threading.Event();awaiter_done=threading.Event();checker_done=threading.Event()
    controller_errors=[]
    def policy(url):
        entered.set()
        try:
            assert release.wait(3)
            raise UnsafeURLError('controlled eventual refusal')
        finally:checker_done.set()
    monkeypatch.setattr(sd,'check_public_url',policy)
    real_resolve=socket.getaddrinfo
    def resolve(host,*args,**kwargs):
        if host=='pending-policy.example':
            entered.set()
            try:
                assert release.wait(3)
                return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
            finally:checker_done.set()
        return real_resolve(host,*args,**kwargs)
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    for name in ['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy']:
        monkeypatch.delenv(name,raising=False)
    def response(request):
        return httpx.Response(302,headers={'Location':'https://pending-policy.example/final'})
    from services.public_download_transport import PublicDownloadTransport
    transport=PublicDownloadTransport() if phase=='pinned_dns' else httpx.MockTransport(response)
    async with httpx.AsyncClient(transport=transport,timeout=2) as client:
        async def pending():
            asyncio.current_task().add_done_callback(lambda _:awaiter_done.set())
            if phase=='pinned_dns':
                await client.get('https://pending-policy.example/model')
            elif phase=='head_redirect':
                await sd._resolve(client,'https://origin.example/model',None)
            else:
                async with sd._stream_get(client,'https://origin.example/model',None,lambda:False,
                                          headers={'Range':'bytes=0-3'} if phase=='get_range' else None):
                    pytest.fail('A pending refused policy check must never yield a body')
        async def ordinary():
            assert await asyncio.to_thread(entered.wait,2)
            raise ValueError('unsupported range')
        def complete_late():
            try:
                if not awaiter_done.wait(2):controller_errors.append('cancelled policy awaiter did not finish')
            finally:release.set()
        worker=threading.Thread(target=complete_late)
        worker.start()
        try:
            operations=[ordinary(),pending()] if ordinary_first else [pending(),ordinary()]
            with pytest.raises(UnsafeURLError):await sd._gather_or_cancel(operations)
        finally:
            release.set();worker.join(3)
        assert not worker.is_alive() and controller_errors==[] and checker_done.wait(1)


@pytest.mark.asyncio
async def test_policy_capacity_waits_for_healthy_calls_and_cancels_queued_admission(monkeypatch):
    from services import _download_policy as policy
    from core.url_safety import UnsafeURLError
    class Slots:
        def __init__(self):
            self.inner=threading.BoundedSemaphore(2)
            self.waiting={name:threading.Event() for name in ['queued','cancelled','expired']}
        def acquire(self,blocking):
            result=self.inner.acquire(blocking=blocking)
            if not result:
                event=self.waiting.get(asyncio.current_task().get_name())
                if event:event.set()
            return result
        def release(self):self.inner.release()
    slots=Slots();monkeypatch.setattr(policy,'_slots',slots)
    release=threading.Event();changed=threading.Condition();calls=[];active=0;peak=0;daemons=[]
    def check(label,block=False):
        nonlocal active,peak
        with changed:
            calls.append(label);daemons.append(threading.current_thread().daemon)
            active+=1;peak=max(peak,active);changed.notify_all()
        try:
            if block:assert release.wait(3)
            return label
        finally:
            with changed:active-=1;changed.notify_all()
    async def call(label,block=False):return await policy.run_check(check,label,block,timeout=2)
    first=[asyncio.create_task(call(str(i),True)) for i in range(2)]
    def first_entered():
        with changed:return changed.wait_for(lambda:len(calls)==2,timeout=1)
    queued=cancelled=None
    try:
        assert await asyncio.to_thread(first_entered)
        queued=asyncio.create_task(call('queued'),name='queued')
        assert await asyncio.to_thread(slots.waiting['queued'].wait,1)
        cancelled=asyncio.create_task(call('cancelled'),name='cancelled')
        assert await asyncio.to_thread(slots.waiting['cancelled'].wait,1)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(cancelled,1)
        with pytest.raises(UnsafeURLError):
            await policy.run_check(check,'expired',timeout=.05)
        assert sorted(calls)==['0','1']
        release.set()
        assert await asyncio.gather(*first)==['0','1']
        assert await asyncio.wait_for(queued,1)=='queued'
        assert await call('reused')=='reused'
        assert not {'cancelled','expired'} & set(calls) and peak<=2 and all(daemons)
    finally:
        release.set()
        await asyncio.gather(*first,*[task for task in [queued,cancelled] if task],return_exceptions=True)


def test_abandoned_policy_worker_does_not_retain_loop_or_lose_capacity(monkeypatch):
    import gc
    import weakref
    from core.url_safety import UnsafeURLError
    from services import _download_policy as policy
    class Slots:
        def __init__(self):self.inner=threading.BoundedSemaphore(1);self.released=threading.Event()
        def acquire(self,blocking):return self.inner.acquire(blocking=blocking)
        def release(self):self.inner.release();self.released.set()
    slots=Slots();monkeypatch.setattr(policy,'_slots',slots)
    entered=threading.Event();release=threading.Event();notifications=[]
    notify=policy._notify
    def observed_notify(*args):notifications.append(True);notify(*args)
    monkeypatch.setattr(policy,'_notify',observed_notify)
    def stuck():
        entered.set()
        assert release.wait(3)
        raise UnsafeURLError('late refusal after closed loop')
    async def run():
        reference=weakref.ref(asyncio.get_running_loop())
        try:await policy.run_check(stuck,timeout=.1)
        except UnsafeURLError:pass
        return reference
    try:
        reference=asyncio.run(run())
        assert entered.is_set() and not release.is_set()
        gc.collect()
        assert reference() is None,'A hung resolver retained its cancelled event loop'
    finally:release.set()
    assert slots.released.wait(1)
    assert notifications==[], 'An abandoned resolver published a late completion'
    assert asyncio.run(policy.run_check(lambda:'capacity reused',timeout=1))=='capacity reused'


@pytest.mark.asyncio
async def test_expired_admission_never_starts_a_late_dns_call(monkeypatch):
    from types import SimpleNamespace
    from core.url_safety import UnsafeURLError
    from services import _download_policy as policy
    clock={'now':0.0};started=threading.Event()
    class Slots:
        def __init__(self):self.released=False
        def acquire(self,blocking):clock['now']=2.0;return True
        def release(self):self.released=True
    slots=Slots()
    monkeypatch.setattr(policy,'_slots',slots)
    monkeypatch.setattr(policy,'time',SimpleNamespace(monotonic=lambda:clock['now']))
    def check():started.set();return 'public'
    with pytest.raises(UnsafeURLError):await policy.run_check(check,timeout=1)
    assert not started.is_set() and slots.released


@pytest.mark.asyncio
async def test_completed_raw_dns_answer_cannot_lose_its_unrun_policy_filter(monkeypatch):
    from core.url_safety import UnsafeURLError
    from services import _download_policy as policy,segmented_download as sd
    from services.public_download_transport import PublicDownloadTransport
    published=asyncio.Event();pending_task=[]
    # Cancel exactly after raw DNS completion, before the public-address filter.
    def cancel_unclassified_result(*_):
        pending_task[0].cancel()
        published.set()
    monkeypatch.setattr(policy,'_POLL',1.0)
    monkeypatch.setattr(policy,'_notify',cancel_unclassified_result)
    release=threading.Event()
    def resolve(*a,**kw):
        assert release.wait(2)
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
    monkeypatch.setattr(socket,'getaddrinfo',resolve)
    class ObservedAsyncio:
        def __getattr__(self,name):return getattr(asyncio,name)
        async def wait(self,*a,**kw):
            release.set()
            return await asyncio.wait(*a,**kw)
    monkeypatch.setattr(policy,'asyncio',ObservedAsyncio())
    async with httpx.AsyncClient(transport=PublicDownloadTransport()) as client:
        async def pending():
            pending_task.append(asyncio.current_task())
            await client.get('https://pending-policy.example/model')
        async def ordinary():
            await published.wait()
            raise ValueError('unsupported range')
        with pytest.raises(UnsafeURLError):await sd._gather_or_cancel([pending(),ordinary()])
        assert published.is_set()
