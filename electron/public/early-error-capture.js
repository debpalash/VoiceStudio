(function installEarlyErrorCapture() {
  const target = window;
  const faults = (target.__voicestudioEarlyFaults ??= []);

  const onError = (event) => {
    faults.push({
      kind: 'error',
      message: event.error?.message || event.message,
      error: event.error,
      filename: event.filename || '',
    });
    event.preventDefault();
  };

  const onRejection = (event) => {
    const reason = event.reason;
    faults.push({
      kind: 'rejection',
      message: reason?.message || String(reason),
      error: reason,
      filename: '',
    });
    event.preventDefault();
  };

  window.addEventListener('error', onError);
  window.addEventListener('unhandledrejection', onRejection);
  target.__voicestudioStopEarlyErrorCapture = () => {
    window.removeEventListener('error', onError);
    window.removeEventListener('unhandledrejection', onRejection);
    delete target.__voicestudioStopEarlyErrorCapture;
  };
})();
