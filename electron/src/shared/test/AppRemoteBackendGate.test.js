import { describe, expect, it } from 'vitest';
import appSource from '../App.jsx?raw';

describe('App remote-backend startup gate', () => {
  it('probes a configured remote before the local setup-status path', () => {
    const remoteProbe = appSource.indexOf('if (remoteBackend)');
    const localSetup = appSource.indexOf("import('./api/setup')", remoteProbe);
    expect(remoteProbe).toBeGreaterThan(-1);
    expect(localSetup).toBeGreaterThan(remoteProbe);
    expect(appSource.slice(remoteProbe, localSetup)).toContain('probeRemoteBackend');
    expect(appSource.slice(remoteProbe, localSetup)).toContain('return;');
  });

  it('does not wait for the local bootstrap when a remote is configured', () => {
    expect(appSource).toContain("if (!remoteBackend && bootstrapStage !== 'ready')");
    expect(appSource).toContain("if (!remoteBackend && bootstrapStage === 'awaiting_setup')");
    expect(appSource).toContain(
      "remoteBackend ? setupChecked && !remoteFailure : bootstrapStage === 'ready'",
    );
    expect(appSource).toContain('if (!backendReady)');
  });

  it('does not contact analytics before the selected backend passes its gate', () => {
    const readyGuard = appSource.indexOf('if (!backendReady) return;');
    const analytics = appSource.indexOf('initAnalyticsFromConsent', readyGuard);
    expect(readyGuard).toBeGreaterThan(-1);
    expect(analytics).toBeGreaterThan(readyGuard);
  });

  it('renders recovery before it can route to the local SetupWizard', () => {
    const recovery = appSource.indexOf('if (remoteFailure)');
    const wizard = appSource.indexOf('if (setupNeeded && backendReady)', recovery);
    expect(recovery).toBeGreaterThan(-1);
    expect(wizard).toBeGreaterThan(recovery);
    expect(appSource.slice(recovery, wizard)).toContain('RemoteBackendRecovery');
  });
});
