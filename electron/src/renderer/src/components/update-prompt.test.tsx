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
  navigate: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), {
    custom: mocks.custom,
    dismiss: mocks.dismissToast,
    info: vi.fn(),
    error: vi.fn(),
  }),
}));
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

  test("error shows friendly text, raw error behind Details, and Retry downloads", async () => {
    await mount();
    push({
      status: "error",
      availableVersion: "0.6.3",
      error: "HTTP 503 <html>",
    });
    show();
    expect(screen.getByText("update.error_network")).toBeTruthy();
    expect(screen.getByText("HTTP 503 <html>").closest("details")).toBeTruthy();
    fireEvent.click(screen.getByText("update.retry"));
    await waitFor(() => expect(mocks.bridge.download).toHaveBeenCalledOnce());
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
