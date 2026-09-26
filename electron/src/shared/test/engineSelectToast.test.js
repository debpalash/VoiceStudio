import { describe, it, expect, vi, beforeEach } from 'vitest';

// A single callable spy that also carries `.success` / `.error`, matching the
// react-hot-toast shape (default + named `toast` are the same object).
const { toastFn } = vi.hoisted(() => {
  const fn = vi.fn();
  fn.success = vi.fn();
  fn.error = vi.fn();
  return { toastFn: fn };
});
vi.mock('react-hot-toast', () => ({ default: toastFn, toast: toastFn }));

import { notifyEngineSelected } from '../utils/engineSelectToast';

// Fake `t` that echoes key + interpolation vars so assertions can see both.
const t = (key, vars) => `${key}:${JSON.stringify(vars || {})}`;

describe('notifyEngineSelected (routing echo → toast)', () => {
  beforeEach(() => {
    toastFn.mockClear();
    toastFn.success.mockClear();
  });

  it('warns (with the reason) when the pick lands on a cpu_fallback', () => {
    notifyEngineSelected(
      {
        active: 'indextts2',
        routing_status: 'cpu_fallback',
        routing_reason: 'engine has no CUDA path; running on CPU',
      },
      t,
      'tts',
    );
    // Warn path uses the bare toast() with an icon; NOT toast.success.
    expect(toastFn.success).not.toHaveBeenCalled();
    expect(toastFn).toHaveBeenCalledTimes(1);
    const [msg, opts] = toastFn.mock.calls[0];
    expect(msg).toContain('engines.selectCpuFallback');
    expect(msg).toContain('indextts2');
    expect(msg).toContain('engine has no CUDA path; running on CPU');
    expect(opts).toMatchObject({ icon: expect.any(String) });
  });

  it('shows a plain success toast for an accelerated pick', () => {
    notifyEngineSelected(
      { active: 'omnivoice', routing_status: 'accelerated', routing_reason: null },
      t,
      'tts',
    );
    expect(toastFn).not.toHaveBeenCalled(); // no warn toast
    expect(toastFn.success).toHaveBeenCalledTimes(1);
    expect(toastFn.success.mock.calls[0][0]).toContain('settings.engine_switched');
    expect(toastFn.success.mock.calls[0][0]).toContain('TTS');
  });

  it('shows a plain success toast for a benign cpu_only pick', () => {
    notifyEngineSelected(
      { active: 'kittentts', routing_status: 'cpu_only', routing_reason: null },
      t,
      'tts',
    );
    expect(toastFn).not.toHaveBeenCalled();
    expect(toastFn.success).toHaveBeenCalledTimes(1);
  });

  // #1226's under-provisioned-VRAM caveat rides on an ACCELERATED verdict, so
  // it used to fall through to the green "switched" toast and vanish. Four of
  // the low-VRAM reports (#1240, #1246, #1248 on 4 GB; #1277 on 6 GB) learned
  // their card was too small only after waiting out the full 300s budget —
  // the warning existed the whole time and was thrown away here.
  it('warns when an accelerated pick carries a VRAM caveat', () => {
    const reason =
      'NVIDIA GeForce GTX 1650 Ti has 4.0 GB VRAM; this engine wants about ' +
      '6 GB. It will run, but expect slow generations that may time out.';
    notifyEngineSelected(
      { active: 'omnivoice', routing_status: 'accelerated', routing_reason: reason },
      t,
      'tts',
    );

    expect(toastFn.success).not.toHaveBeenCalled();
    expect(toastFn).toHaveBeenCalledTimes(1);
    const [msg, opts] = toastFn.mock.calls[0];
    expect(msg).toContain('engines.selectWithCaveat');
    expect(msg).toContain('omnivoice');
    expect(msg).toContain('4.0 GB VRAM');
    expect(opts).toMatchObject({ icon: expect.any(String) });
    // Long enough to actually read — it names the limit and the ways around it.
    expect(opts.duration).toBeGreaterThanOrEqual(8000);
  });

  it('warns on a kernel-risk caveat too, not just the VRAM one', () => {
    notifyEngineSelected(
      {
        active: 'omnivoice',
        routing_status: 'accelerated',
        routing_reason: "CUDA selected, but: GPU (sm_61) not in this torch build's archs",
      },
      t,
      'tts',
    );
    expect(toastFn.success).not.toHaveBeenCalled();
    expect(toastFn).toHaveBeenCalledTimes(1);
  });

  // Greptile P1: a bare `routing_reason` test also fires on benign verdicts.
  // Routing rule 5 gives a Windows DirectML host a cpu_only + reason on a
  // perfectly normal pick, and rule 6 attaches one to `unavailable` — neither
  // is a hardware warning. routing_notice() in engine_routing.py is the
  // canonical predicate: cpu_fallback always, accelerated only with a reason.
  it('stays silent for a benign cpu_only pick that carries a reason', () => {
    notifyEngineSelected(
      {
        active: 'kittentts',
        routing_status: 'cpu_only',
        routing_reason:
          'DirectML GPU present; engine routes via torch CPU path ' +
          '(DirectML acceleration not wired into routing)',
      },
      t,
      'tts',
    );
    expect(toastFn).not.toHaveBeenCalled();
    expect(toastFn.success).toHaveBeenCalledTimes(1);
  });

  it('does not warn on an unavailable verdict either', () => {
    notifyEngineSelected(
      {
        active: 'gpt-sovits',
        routing_status: 'unavailable',
        routing_reason: 'requires cuda; this host has cpu',
      },
      t,
      'tts',
    );
    expect(toastFn).not.toHaveBeenCalled();
    expect(toastFn.success).toHaveBeenCalledTimes(1);
  });

  it('shows a plain success toast for a legacy response with no routing_status', () => {
    notifyEngineSelected({ active: 'legacy' }, t, 'asr');
    expect(toastFn).not.toHaveBeenCalled();
    expect(toastFn.success).toHaveBeenCalledTimes(1);
    expect(toastFn.success.mock.calls[0][0]).toContain('ASR');
  });
});
