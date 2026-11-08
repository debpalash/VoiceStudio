import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  act,
} from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { UpdateState } from "../../../preload/index.d";

const mocks = vi.hoisted(() => ({
  listener: undefined as ((s: UpdateState) => void) | undefined,
  state: {
    status: "idle",
    currentVersion: "0.5.2",
    progress: 0,
  } as UpdateState,
  bridge: {
    getState: vi.fn(),
    check: vi.fn(),
    download: vi.fn(),
    install: vi.fn(),
    dismiss: vi.fn(),
  },
  custom: vi.fn(),
  dismissToast: vi.fn(),
  info: vi.fn(),
  navigate: vi.fn(),
  api: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), {
    custom: mocks.custom,
    dismiss: mocks.dismissToast,
    info: mocks.info,
    error: vi.fn(),
  }),
}));
vi.mock("@/lib/api/client", () => {
  class ApiError extends Error {
    constructor(readonly status: number) {
      super(`HTTP ${status}`);
    }
  }
  return { ApiError, apiJson: mocks.api };
});
vi.mock("@/router", () => ({ router: { navigate: mocks.navigate } }));
vi.mock("@/components/bridge", () => ({
  getBridge: () => ({
    updates: {
      ...mocks.bridge,
      onState: (l: (s: UpdateState) => void) => {
        mocks.listener = l;
        return () => {};
      },
    },
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, v?: { version?: string; pct?: number }) =>
      v?.version
        ? `${key} ${v.version}`
        : v?.pct !== undefined
          ? `${key} ${v.pct}`
          : key,
  }),
}));

import { UpdatePrompt } from "./update-prompt";

const base = {
  currentVersion: "0.5.2",
  availableVersion: "0.5.3",
  progress: 0,
};
const push = (s: Partial<UpdateState>) =>
  act(() => mocks.listener!({ ...base, ...s } as UpdateState));
const lastToast = () => {
  const [factory, opts] = mocks.custom.mock.calls.at(-1)!;
  return { opts, ui: (factory as (id: string) => ReactNode)("x") };
};
const show = () => render(<>{lastToast().ui}</>);

describe("UpdatePrompt", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listener = undefined;
    mocks.bridge.getState.mockResolvedValue({
      status: "idle",
      currentVersion: "0.5.2",
      progress: 0,
    });
    for (const fn of [
      mocks.bridge.download,
      mocks.bridge.check,
      mocks.bridge.dismiss,
    ])
      fn.mockResolvedValue({});
    mocks.bridge.install.mockResolvedValue(undefined);
    mocks.api.mockImplementation(async (path: string) =>
      path.startsWith("/batch") ? [] : { jobs: [] },
    );
  });
  afterEach(cleanup);

  const mount = async () => {
    render(<UpdatePrompt />);
    await waitFor(() => expect(mocks.listener).toBeDefined());
  };

  test("available shows Download and Later; both call the bridge", async () => {
    await mount();
    push({ status: "available" });
    expect(lastToast().opts).toMatchObject({
      id: "desktop-update-0.5.3",
      duration: Infinity,
    });
    show();
    expect(screen.getByText("update.prompt_available 0.5.3")).toBeTruthy();
    fireEvent.click(screen.getByText("update.download"));
    await waitFor(() => expect(mocks.bridge.download).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByText("update.toast_later"));
    expect(mocks.bridge.dismiss).toHaveBeenCalledOnce();
    expect(mocks.dismissToast).toHaveBeenCalledWith("desktop-update-0.5.3");
    // Later keeps this version hidden for the rest of the launch.
    mocks.custom.mockClear();
    push({ status: "available" });
    expect(mocks.custom).not.toHaveBeenCalled();
  });

  test("later versions and states for the same version reuse one toast id", async () => {
    await mount();
    push({ status: "available", availableVersion: "0.6.0" });
    push({
      status: "downloading",
      availableVersion: "0.6.0",
      progress: 40,
      transferredBytes: 4 * 1024 ** 2,
      totalBytes: 10 * 1024 ** 2,
    });
    push({ status: "downloaded", availableVersion: "0.6.0", progress: 100 });
    const ids = mocks.custom.mock.calls.map((c) => c[1].id);
    expect(new Set(ids)).toEqual(new Set(["desktop-update-0.6.0"]));
    expect(mocks.dismissToast).not.toHaveBeenCalled();
  });

  test("downloading shows progress and no action buttons except hide", async () => {
    await mount();
    push({
      status: "downloading",
      availableVersion: "0.6.1",
      progress: 40,
      transferredBytes: 4 * 1024 ** 2,
      totalBytes: 10 * 1024 ** 2,
    });
    show();
    expect(screen.getByText("update.downloading 40")).toBeTruthy();
    expect(screen.getByText("4 MB / 10 MB")).toBeTruthy();
    expect(screen.queryByText("update.download")).toBeNull();
    expect(screen.queryByText("update.restart")).toBeNull();
    expect(screen.getByText("update.prompt_hide")).toBeTruthy();
  });

  test("downloaded offers Restart to update and opens Settings via More", async () => {
    await mount();
    push({ status: "downloaded", availableVersion: "0.6.2" });
    show();
    expect(screen.getByText("update.prompt_ready")).toBeTruthy();
    fireEvent.click(screen.getByText("update.restart"));
    await waitFor(() => expect(mocks.bridge.install).toHaveBeenCalledOnce());
    fireEvent.click(screen.getByText("update.prompt_more"));
    expect(mocks.navigate).toHaveBeenCalledWith({ to: "/settings/updates" });
  });

  test("error shows friendly text, raw error behind Details, and Retry re-checks then downloads", async () => {
    await mount();
    push({
      status: "error",
      availableVersion: "0.6.3",
      error: "HTTP 503 <html>",
    });
    show();
    expect(screen.getByText("update.error_network")).toBeTruthy();
    expect(screen.getByText("HTTP 503 <html>").closest("details")).toBeTruthy();
    // The main-process updater downloads only from `available`, never `error`.
    mocks.bridge.check.mockResolvedValue({ ...base, status: "available" });
    fireEvent.click(screen.getByText("update.retry"));
    await waitFor(() => expect(mocks.bridge.download).toHaveBeenCalledOnce());
    expect(mocks.bridge.check.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.bridge.download.mock.invocationCallOrder[0],
    );
  });

  test("Retry does not download when the re-check finds no update", async () => {
    await mount();
    push({ status: "error", availableVersion: "0.6.3", error: "HTTP 503" });
    show();
    mocks.bridge.check.mockResolvedValue({ ...base, status: "error" });
    fireEvent.click(screen.getByText("update.retry"));
    await waitFor(() => expect(mocks.bridge.check).toHaveBeenCalledOnce());
    expect(mocks.bridge.download).not.toHaveBeenCalled();
  });

  test("a hidden download stays hidden through progress and returns when ready", async () => {
    await mount();
    const downloading = { status: "downloading" as const, availableVersion: "0.6.5" };
    push({ ...downloading, progress: 10 });
    show();
    fireEvent.click(screen.getByText("update.prompt_hide"));
    expect(mocks.dismissToast).toHaveBeenCalledWith("desktop-update-0.6.5");
    mocks.custom.mockClear();
    push({ ...downloading, progress: 50 });
    push({ ...downloading, progress: 90 });
    expect(mocks.custom).not.toHaveBeenCalled();
    push({ status: "downloaded", availableVersion: "0.6.5", progress: 100 });
    expect(mocks.custom).toHaveBeenCalledOnce();
  });

  test("Restart waits while the backend is still installing a model", async () => {
    mocks.api.mockImplementation(async (path: string) =>
      path.startsWith("/batch") ? [] : { jobs: [{ state: "downloading" }] },
    );
    await mount();
    push({ status: "downloaded", availableVersion: "0.6.6" });
    show();
    fireEvent.click(screen.getByText("update.restart"));
    await waitFor(() => expect(mocks.info).toHaveBeenCalledWith("update.busy"));
    expect(mocks.bridge.install).not.toHaveBeenCalled();
  });

  test.each([
    [500, true],
    [0, false],
    [404, false],
  ])(
    "Restart with a work-status failure %s defers: %s",
    async (status, defers) => {
      const { ApiError } = (await import("@/lib/api/client")) as unknown as {
        ApiError: new (s: number) => Error;
      };
      mocks.api.mockRejectedValue(new ApiError(status));
      await mount();
      push({ status: "downloaded", availableVersion: `0.7.${status}` });
      show();
      fireEvent.click(screen.getByText("update.restart"));
      if (defers) {
        await waitFor(() => expect(mocks.info).toHaveBeenCalledWith("update.busy"));
        expect(mocks.bridge.install).not.toHaveBeenCalled();
      } else {
        await waitFor(() => expect(mocks.bridge.install).toHaveBeenCalledOnce());
      }
    },
  );

  test("Restart defers when the work status times out", async () => {
    mocks.api.mockRejectedValue(new DOMException("timed out", "TimeoutError"));
    await mount();
    push({ status: "downloaded", availableVersion: "0.7.9" });
    show();
    fireEvent.click(screen.getByText("update.restart"));
    await waitFor(() => expect(mocks.info).toHaveBeenCalledWith("update.busy"));
    expect(mocks.bridge.install).not.toHaveBeenCalled();
  });

  test("a hidden error returns on the next failed check", async () => {
    await mount();
    push({ status: "error", availableVersion: undefined, error: "x" });
    show();
    fireEvent.click(screen.getByText("update.toast_later"));
    mocks.custom.mockClear();
    push({ status: "error", availableVersion: undefined, error: "x" });
    expect(mocks.custom).not.toHaveBeenCalled();
    push({ status: "checking", availableVersion: undefined });
    push({ status: "error", availableVersion: undefined, error: "x" });
    expect(mocks.custom).toHaveBeenCalledOnce();
  });

  test("Restart waits while a server batch job is active", async () => {
    mocks.api.mockImplementation(async (path: string) =>
      path.startsWith("/batch") ? [{ id: "b1" }] : { jobs: [] },
    );
    await mount();
    push({ status: "downloaded", availableVersion: "0.6.7" });
    show();
    fireEvent.click(screen.getByText("update.restart"));
    await waitFor(() => expect(mocks.info).toHaveBeenCalledWith("update.busy"));
    expect(mocks.bridge.install).not.toHaveBeenCalled();
  });

  test("error without a version retries the check", async () => {
    await mount();
    push({ status: "error", availableVersion: undefined, error: "x" });
    show();
    fireEvent.click(screen.getByText("update.retry"));
    await waitFor(() => expect(mocks.bridge.check).toHaveBeenCalledOnce());
  });

  test.each(["unsupported", "idle", "checking"] as const)(
    "%s shows nothing",
    async (status) => {
      await mount();
      push({ status, availableVersion: undefined });
      expect(mocks.custom).not.toHaveBeenCalled();
    },
  );
});
