import { describe, it, expect, vi, beforeEach } from 'vitest';

const { toastFn, listEnginesMock } = vi.hoisted(() => {
  const fn = vi.fn();
  fn.success = vi.fn();
  fn.error = vi.fn();
  return { toastFn: fn, listEnginesMock: vi.fn() };
});
vi.mock('react-hot-toast', () => ({ default: toastFn, toast: toastFn }));
vi.mock('../api/engines', () => ({ listEngines: listEnginesMock }));
vi.mock('i18next', () => ({
  default: { t: (key, vars) => `${key}:${JSON.stringify(vars || {})}` },
}));

import {
  warnIfEngineUnderProvisioned,
  onEngineSelected,
  _resetPreflight,
} from '../utils/generatePreflight';

const VRAM_CAVEAT =
  'NVIDIA GeForce RTX 2060 has 6.0 GB VRAM; this engine wants about 6 GB. ' +
  'It will run, but expect slow generations that may time out.';

function engines(active, backends) {
  return { tts: { active, backends } };
}

describe('warnIfEngineUnderProvisioned (generate-time preflight)', () => {
  beforeEach(() => {
    toastFn.mockClear();
    listEnginesMock.mockReset();
    _resetPreflight();
  });

  // The whole point of #1240/#1246/#1248/#1277/#1283/#1284: the caveat existed
  // the entire time, but only the engine-PICK path showed it. Users whose
  // engine was already selected waited out 300s to learn their card was small.
  it('warns when the already-selected engine carries a VRAM caveat', async () => {
    listEnginesMock.mockResolvedValue(
      engines('omnivoice', [
        { id: 'omnivoice', routing_status: 'accelerated', routing_reason: VRAM_CAVEAT },
      ]),
    );

    await warnIfEngineUnderProvisioned();

    expect(toastFn).toHaveBeenCalledTimes(1);
    const [msg, opts] = toastFn.mock.calls[0];
    expect(msg).toContain('engines.generateCaveat');
    expect(msg).toContain('omnivoice');
    expect(msg).toContain('6.0 GB VRAM');
    // Long enough to read a sentence about hardware.
    expect(opts.duration).toBeGreaterThanOrEqual(8000);
  });

  it('warns only once per engine+reason, not on every synth', async () => {
    listEnginesMock.mockResolvedValue(
      engines('omnivoice', [
        { id: 'omnivoice', routing_status: 'accelerated', routing_reason: VRAM_CAVEAT },
      ]),
    );

    await warnIfEngineUnderProvisioned();
    await warnIfEngineUnderProvisioned();
    await warnIfEngineUnderProvisioned();

    expect(toastFn).toHaveBeenCalledTimes(1);
  });

  it('stays silent for a healthy accelerated engine', async () => {
    listEnginesMock.mockResolvedValue(
      engines('omnivoice', [
        { id: 'omnivoice', routing_status: 'accelerated', routing_reason: null },
      ]),
    );

    await warnIfEngineUnderProvisioned();
    expect(toastFn).not.toHaveBeenCalled();
  });

  // Benign verdicts carry reasons too — routing rule 5 (DirectML) and rule 6.
  it('stays silent for a benign cpu_only reason', async () => {
    listEnginesMock.mockResolvedValue(
      engines('kittentts', [
        {
          id: 'kittentts',
          routing_status: 'cpu_only',
          routing_reason: 'DirectML GPU present; engine routes via torch CPU path',
        },
      ]),
    );

    await warnIfEngineUnderProvisioned();
    expect(toastFn).not.toHaveBeenCalled();
  });

  it('warns on a cpu_fallback engine', async () => {
    listEnginesMock.mockResolvedValue(
      engines('indextts2', [
        {
          id: 'indextts2',
          routing_status: 'cpu_fallback',
          routing_reason: 'engine has no CUDA path; running on CPU',
        },
      ]),
    );

    await warnIfEngineUnderProvisioned();
    expect(toastFn).toHaveBeenCalledTimes(1);
  });

  // A warning must never be able to break the thing it warns about.
  it('never throws when the engine list is unreachable', async () => {
    listEnginesMock.mockRejectedValue(new Error('backend down'));
    await expect(warnIfEngineUnderProvisioned()).resolves.toBeUndefined();
    expect(toastFn).not.toHaveBeenCalled();
  });

  it('tolerates a response with no active engine', async () => {
    listEnginesMock.mockResolvedValue({ tts: { active: null, backends: [] } });
    await expect(warnIfEngineUnderProvisioned()).resolves.toBeUndefined();
    expect(toastFn).not.toHaveBeenCalled();
  });

  it('fetches the engine list once, not once per synth', async () => {
    listEnginesMock.mockResolvedValue(
      engines('omnivoice', [
        { id: 'omnivoice', routing_status: 'accelerated', routing_reason: null },
      ]),
    );

    await warnIfEngineUnderProvisioned();
    await warnIfEngineUnderProvisioned();

    expect(listEnginesMock).toHaveBeenCalledTimes(1);
  });
});

describe('preflight cache invalidation', () => {
  beforeEach(() => {
    toastFn.mockClear();
    listEnginesMock.mockReset();
    _resetPreflight();
  });

  // Greptile P1: with a 60s TTL, switching engines and generating immediately
  // warned about the engine you just LEFT — or stayed silent about the one you
  // just chose.
  it('refetches after an engine change instead of serving the old engine', async () => {
    listEnginesMock.mockResolvedValueOnce(
      engines('kittentts', [{ id: 'kittentts', routing_status: 'cpu_only', routing_reason: null }]),
    );
    await warnIfEngineUnderProvisioned();
    expect(toastFn).not.toHaveBeenCalled();

    onEngineSelected('omnivoice', null);

    listEnginesMock.mockResolvedValueOnce(
      engines('omnivoice', [
        { id: 'omnivoice', routing_status: 'accelerated', routing_reason: VRAM_CAVEAT },
      ]),
    );
    await warnIfEngineUnderProvisioned();

    expect(listEnginesMock).toHaveBeenCalledTimes(2);
    expect(toastFn).toHaveBeenCalledTimes(1);
    expect(toastFn.mock.calls[0][0]).toContain('omnivoice');
  });

  it('does not repeat a caveat the select toast just showed', async () => {
    onEngineSelected('omnivoice', VRAM_CAVEAT);
    listEnginesMock.mockResolvedValue(
      engines('omnivoice', [
        { id: 'omnivoice', routing_status: 'accelerated', routing_reason: VRAM_CAVEAT },
      ]),
    );

    await warnIfEngineUnderProvisioned();
    expect(toastFn).not.toHaveBeenCalled();
  });

  // A rejected promise cached for the full TTL silences the caveat for a
  // minute after the backend comes back.
  it('retries after a transient engine-list failure', async () => {
    listEnginesMock.mockRejectedValueOnce(new Error('backend restarting'));
    await warnIfEngineUnderProvisioned();
    expect(toastFn).not.toHaveBeenCalled();

    listEnginesMock.mockResolvedValueOnce(
      engines('omnivoice', [
        { id: 'omnivoice', routing_status: 'accelerated', routing_reason: VRAM_CAVEAT },
      ]),
    );
    await warnIfEngineUnderProvisioned();

    expect(listEnginesMock).toHaveBeenCalledTimes(2);
    expect(toastFn).toHaveBeenCalledTimes(1);
  });
});

describe('CPU-only host with long text', () => {
  beforeEach(() => {
    toastFn.mockClear();
    listEnginesMock.mockReset();
    _resetPreflight();
  });

  const LONG = 'a'.repeat(1500);
  const SHORT = 'a'.repeat(200);
  const cpuHost = () =>
    engines('omnivoice', [{ id: 'omnivoice', routing_status: 'cpu_only', routing_reason: null }]);

  // #1299 / #1260: a CPU-only host is a BENIGN routing verdict, so the
  // under-provisioned-GPU check correctly stays silent — and these users got
  // no warning at all before burning the whole 300s budget.
  it('warns on long text when the host runs on CPU', async () => {
    listEnginesMock.mockResolvedValue(cpuHost());
    await warnIfEngineUnderProvisioned(LONG);

    expect(toastFn).toHaveBeenCalledTimes(1);
    expect(toastFn.mock.calls[0][0]).toContain('engines.cpuLongText');
    expect(toastFn.mock.calls[0][0]).toContain('omnivoice');
  });

  it('stays quiet for ordinary short text on the same CPU host', async () => {
    listEnginesMock.mockResolvedValue(cpuHost());
    await warnIfEngineUnderProvisioned(SHORT);
    expect(toastFn).not.toHaveBeenCalled();
  });

  it('stays quiet for long text on an accelerated host', async () => {
    listEnginesMock.mockResolvedValue(
      engines('omnivoice', [
        { id: 'omnivoice', routing_status: 'accelerated', routing_reason: null },
      ]),
    );
    await warnIfEngineUnderProvisioned(LONG);
    expect(toastFn).not.toHaveBeenCalled();
  });

  it('warns once, not on every long synth', async () => {
    listEnginesMock.mockResolvedValue(cpuHost());
    await warnIfEngineUnderProvisioned(LONG);
    await warnIfEngineUnderProvisioned(LONG);
    expect(toastFn).toHaveBeenCalledTimes(1);
  });

  it('prefers the hardware caveat when there is one', async () => {
    listEnginesMock.mockResolvedValue(
      engines('indextts2', [
        {
          id: 'indextts2',
          routing_status: 'cpu_fallback',
          routing_reason: 'engine has no CUDA path; running on CPU',
        },
      ]),
    );
    await warnIfEngineUnderProvisioned(LONG);

    // The hardware caveat wins: one toast, and it names the routing reason
    // rather than the generic "long text on CPU" advice.
    expect(toastFn).toHaveBeenCalledTimes(1);
    expect(toastFn.mock.calls[0][0]).toContain('engines.generateCaveat');
    expect(toastFn.mock.calls[0][0]).toContain('no CUDA path');
    expect(toastFn.mock.calls[0][0]).not.toContain('cpuLongText');
  });
});

// #1787/#1778: an M3/MPS host that hits the flat GPU-family budget on long
// text got no warning at all — `effective_device: 'mps'` is neither
// `routing_status: 'cpu_only'` (the CPU branch) nor a benign accelerated
// verdict with a reason, so both existing checks stayed silent.
describe('MPS host with long text', () => {
  beforeEach(() => {
    toastFn.mockClear();
    listEnginesMock.mockReset();
    _resetPreflight();
  });

  const LONG = 'a'.repeat(1500);
  const SHORT = 'a'.repeat(200);
  const mpsHost = () =>
    engines('omnivoice', [
      {
        id: 'omnivoice',
        routing_status: 'accelerated',
        routing_reason: null,
        effective_device: 'mps',
      },
    ]);

  it('warns on long text when the host runs on MPS', async () => {
    listEnginesMock.mockResolvedValue(mpsHost());
    await warnIfEngineUnderProvisioned(LONG);

    expect(toastFn).toHaveBeenCalledTimes(1);
    expect(toastFn.mock.calls[0][0]).toContain('engines.mpsLongText');
    expect(toastFn.mock.calls[0][0]).toContain('omnivoice');
  });

  it('stays quiet for ordinary short text on the same MPS host', async () => {
    listEnginesMock.mockResolvedValue(mpsHost());
    await warnIfEngineUnderProvisioned(SHORT);
    expect(toastFn).not.toHaveBeenCalled();
  });

  it('warns once, not on every long synth', async () => {
    listEnginesMock.mockResolvedValue(mpsHost());
    await warnIfEngineUnderProvisioned(LONG);
    await warnIfEngineUnderProvisioned(LONG);
    expect(toastFn).toHaveBeenCalledTimes(1);
  });

  it('stays quiet for long text on a CUDA host', async () => {
    listEnginesMock.mockResolvedValue(
      engines('omnivoice', [
        {
          id: 'omnivoice',
          routing_status: 'accelerated',
          routing_reason: null,
          effective_device: 'cuda',
        },
      ]),
    );
    await warnIfEngineUnderProvisioned(LONG);
    expect(toastFn).not.toHaveBeenCalled();
  });
});

describe('CPU-tuned engines do not recommend themselves', () => {
  beforeEach(() => {
    toastFn.mockClear();
    listEnginesMock.mockReset();
    _resetPreflight();
  });

  const LONG = 'a'.repeat(1500);
  const onCpu = (id) => engines(id, [{ id, routing_status: 'cpu_only', routing_reason: null }]);

  // Greptile P1: the generic advice is "try a CPU-tuned engine (VoiceStudio
  // GGUF, Supertonic-3)". Showing that to someone already running one of them
  // is advice to switch to what they are using.
  it.each(['omnivoice-gguf', 'supertonic3'])(
    'drops the self-referential suggestion on %s',
    async (id) => {
      listEnginesMock.mockResolvedValue(onCpu(id));
      await warnIfEngineUnderProvisioned(LONG);

      expect(toastFn).toHaveBeenCalledTimes(1);
      const msg = toastFn.mock.calls[0][0];
      expect(msg).toContain('engines.cpuLongTextTuned');
      expect(msg).not.toContain('engines.cpuLongText:');
    },
  );

  it('still suggests a CPU-tuned engine to everyone else', async () => {
    listEnginesMock.mockResolvedValue(onCpu('omnivoice'));
    await warnIfEngineUnderProvisioned(LONG);

    expect(toastFn).toHaveBeenCalledTimes(1);
    expect(toastFn.mock.calls[0][0]).toContain('engines.cpuLongText');
  });
});
