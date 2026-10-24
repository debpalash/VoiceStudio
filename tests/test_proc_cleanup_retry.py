"""Exercise real subprocess helpers without importing model runtimes."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('kind,platform', [('factory', 'darwin'), ('streaming', 'darwin'), ('ffmpeg', 'darwin'), ('ffmpeg', 'win32')])
def test_failed_caller_cleanup_keeps_process_available_to_next_abort(kind, platform):
    from services import proc_registry as registry

    tree = ast.parse((Path(__file__).parents[1] / 'backend/services/dub_pipeline.py').read_text())
    name = 'run_proc_streaming_stderr' if kind == 'streaming' else 'run_proc_factory'
    if kind == 'ffmpeg':
        tree = ast.parse((Path(__file__).parents[1] / 'backend/services/ffmpeg_utils.py').read_text())
        name = 'run_ffmpeg'
    definition = next(node for node in tree.body if getattr(node, 'name', None) == name)
    jid = f'cleanup-retry-{kind}-{platform}'

    class Process:
        returncode = None
        uses_sync_pipes = True
        stdout = SimpleNamespace(close=lambda: None)
        attempts = 0

        async def communicate(self):
            raise asyncio.CancelledError()

        def kill(self):
            self.attempts += 1
            if self.attempts == 1:
                raise PermissionError('controlled cleanup failure')
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    namespace = {
        'asyncio': asyncio, '_spawn_with_retry': spawn,
        '_get_semaphore': lambda: asyncio.Semaphore(1),
        'register_proc': registry.register_proc, 'unregister_proc': registry.unregister_proc,
        'AsyncIterator': object,
        'local_inputs_only': lambda cmd, **kwargs: cmd,
        'externalize_long_filter_complex': lambda cmd: (cmd, None),
        '_media_tool_kind': lambda cmd: 'ffmpeg',
        'sys': SimpleNamespace(platform=platform), 'os': __import__('os'),
        'logger': __import__('logging').getLogger('owned-proc-test'),
    }
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), definition], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), '<actual-subprocess-helper>', 'exec'), namespace)

    async def run():
        with pytest.raises(PermissionError, match='controlled cleanup failure'):
            if kind == 'streaming':
                async for _ in namespace[name](jid, ['owned-stub']):
                    pass
            elif kind == 'ffmpeg':
                await namespace[name](['owned-stub'], job_id=jid)
            else:
                await namespace[name](jid)(['owned-stub'])
        assert registry.has_active_procs(jid)
        registry.kill_job_procs(jid)
        assert process.attempts == 2
        assert process.returncode == -9
        assert not registry.has_active_procs(jid)

    try:
        asyncio.run(run())
    finally:
        registry._active_procs.pop(jid, None)


def test_unregister_keeps_live_process_and_removes_confirmed_exit():
    from services import proc_registry as registry
    jid = 'cleanup-confirmed-exit'
    process = SimpleNamespace(returncode=None)
    registry.register_proc(jid, process)
    try:
        registry.unregister_proc(jid, process)
        assert registry.has_active_procs(jid)
        process.returncode = 0
        registry.unregister_proc(jid, process)
        assert not registry.has_active_procs(jid)
    finally:
        registry._active_procs.pop(jid, None)


def test_ingest_final_cleanup_retains_repeated_kill_failure_until_next_abort():
    from services import proc_registry as registry

    tree = ast.parse((Path(__file__).parents[1] / 'backend/services/dub_pipeline.py').read_text())
    ingest = next(node for node in tree.body if getattr(node, 'name', None) == 'ingest_pipeline')
    cleanup = next(node for node in reversed(ingest.body) if isinstance(node, ast.Try)).finalbody
    jid = 'ingest-cleanup-retry'

    class Process:
        returncode = None
        attempts = 0

        def kill(self):
            self.attempts += 1
            if self.attempts < 3:
                raise PermissionError('controlled repeated failure')
            self.returncode = -9

    process = Process()
    registry.register_proc(jid, process)
    namespace = {
        'source': {}, 'job_id': jid,
        '_delete_cookie_export': lambda value: None, 'end_ingest': lambda value: None,
        'kill_job_procs': registry.kill_job_procs,
        '_active_procs_lock': registry._active_procs_lock, '_active_procs': registry._active_procs,
    }
    module = ast.Module(body=cleanup, type_ignores=[])
    try:
        registry.kill_job_procs(jid)
        exec(compile(ast.fix_missing_locations(module), '<actual-ingest-finally>', 'exec'), namespace)
        assert process.attempts == 2
        assert registry.has_active_procs(jid)
        registry.kill_job_procs(jid)
        assert process.returncode == -9
        assert not registry.has_active_procs(jid)
    finally:
        registry._active_procs.pop(jid, None)
