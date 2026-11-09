"""The stub-tool harness for the GGUF build tests proves which tools ran (#3549)."""
from build_script_stubs import WRONG_TOOL, run_bash, stage_build_script, write_lf


def test_a_missing_stub_is_refused_before_the_script_runs(tmp_path):
    write_lf(tmp_path / "probe.sh", 'touch "$PWD/ran"\ngit --version\n')
    result = run_bash(tmp_path, ["probe.sh"], tools={"cmake": "exit 0"}, expect=("git", "cmake"))
    assert result.returncode == WRONG_TOOL, result.stdout + result.stderr
    assert "mock git resolves to" in result.stderr
    assert not (tmp_path / "ran").exists()


def test_declared_stubs_are_the_tools_the_script_invokes(tmp_path):
    write_lf(tmp_path / "probe.sh", 'git clone x\ncmake --version\n')
    log = tmp_path / "calls.log"
    tools = {name: f'printf "{name} %s\\n" "$*" >> "$CALL_LOG"' for name in ("git", "cmake")}
    result = run_bash(tmp_path, ["probe.sh"], tools=tools, env={"CALL_LOG": log.as_posix()})
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == ["git clone x", "cmake --version"]


def test_stubs_and_staged_scripts_use_lf_endings(tmp_path):
    write_lf(tmp_path / "stub", "a\r\nb\r\n")
    assert (tmp_path / "stub").read_bytes() == b"a\nb\n"
    stage_build_script(tmp_path)
    staged = list((tmp_path / "scripts").rglob("*.sh"))
    assert staged and all(b"\r" not in path.read_bytes() for path in staged)
