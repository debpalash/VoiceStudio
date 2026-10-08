"""Real children on the unsupported-subprocess loop retain asyncio exit status."""
import asyncio
import sys
import pytest

class UnsupportedSubprocessLoop(asyncio.SelectorEventLoop):
    async def _make_subprocess_transport(self, *args, **kwargs):
        raise NotImplementedError

@pytest.mark.parametrize("code", [0, 23])
def test_public_spawn_wait_updates_returncode_on_fallback(code):
    from services.ffmpeg_utils import spawn_subprocess
    async def run():
        proc = await spawn_subprocess(sys.executable, "-c", f"raise SystemExit({code})")
        assert proc.uses_sync_pipes
        assert await asyncio.wait_for(proc.wait(), 5) == code
        assert proc.returncode == code
        assert await proc.wait() == code
    with asyncio.Runner(loop_factory=UnsupportedSubprocessLoop) as runner:
        runner.run(run())

def test_fallback_communicate_keeps_exit_code_and_real_output():
    from services.ffmpeg_utils import spawn_subprocess
    async def run():
        proc = await spawn_subprocess(sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'media child\\n'); raise SystemExit(7)", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 5)
        assert out == b"media child\n"
        assert err == b""
        assert proc.returncode == 7
    with asyncio.Runner(loop_factory=UnsupportedSubprocessLoop) as runner:
        runner.run(run())
