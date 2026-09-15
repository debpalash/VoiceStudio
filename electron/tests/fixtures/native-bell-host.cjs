const { app, BrowserWindow } = require('electron');
app.whenReady().then(() => {
  const window = new BrowserWindow({
    width: 1100,
    height: 760,
    x: 60,
    y: 60,
    titleBarStyle: 'hidden',
  });
  window.loadURL('about:blank');
});
