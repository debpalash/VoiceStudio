import shell from '../../scripts/install.sh';
import powershell from '../../scripts/install.ps1';

const html = `<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Install VoiceStudio Electron</title>
<style>body{font:16px system-ui;max-width:54rem;margin:4rem auto;padding:0 1.5rem;background:#14121f;color:#e8e6f0}pre{padding:1rem;background:#221f33;overflow:auto}a{color:#b9a7ff}</style>
<h1>Install VoiceStudio Electron</h1>
<h2>macOS / Linux</h2>
<p>Latest release:</p><pre>curl -fsSL https://voicestudio.sh/install | sh</pre>
<p>A specific Electron release (replace X.Y.Z):</p><pre>curl -fsSL https://voicestudio.sh/install | sh -s -- --version X.Y.Z</pre>
<p>Build main and install:</p><pre>curl -fsSL https://voicestudio.sh/install | sh -s -- --main</pre>
<p>Uninstall (keep settings, models and projects):</p><pre>curl -fsSL https://voicestudio.sh/install | sh -s -- --uninstall</pre>
<h2>Windows PowerShell</h2>
<pre>irm https://voicestudio.sh/install.ps1 | iex</pre>
<pre>$env:VOICESTUDIO_VERSION='X.Y.Z'; irm https://voicestudio.sh/install.ps1 | iex</pre>
<pre>$env:VOICESTUDIO_INSTALL_MODE='main'; irm https://voicestudio.sh/install.ps1 | iex</pre>
<p>Uninstall (clear any version override first):</p><pre>Remove-Item Env:VOICESTUDIO_VERSION -ErrorAction SilentlyContinue
$env:VOICESTUDIO_INSTALL_MODE='uninstall'; irm https://voicestudio.sh/install.ps1 | iex</pre>
<p>Main builds require Git, Node.js 22+, Bun, Rust/Cargo and platform build tools.
Release installation verifies published checksums. Only Electron releases are supported.</p>
<p>Quit VoiceStudio before installing. Existing models, projects and settings are preserved.
Open the installed app to configure its backend and choose models.</p>
<p><a href="https://github.com/debpalash/VoiceStudio">GitHub and build prerequisites</a></p></html>`;

export default {
  fetch(request) {
    const { pathname } = new URL(request.url);
    if (!['/install', '/install.sh', '/install.ps1'].includes(pathname)) {
      return Response.redirect(`https://github.com/debpalash/VoiceStudio${pathname}`, 302);
    }
    if (!['GET', 'HEAD'].includes(request.method)) {
      return new Response('Method not allowed', { status: 405, headers: { Allow: 'GET, HEAD' } });
    }
    const landing = pathname === '/install' && (request.headers.get('Accept') || '').includes('text/html');
    const windows = pathname === '/install.ps1' ||
      (pathname === '/install' && /PowerShell|Pwsh/i.test(request.headers.get('User-Agent') || ''));
    const body = landing ? html : windows ? powershell : shell;
    return new Response(request.method === 'HEAD' ? null : body, {
      headers: {
        'Content-Type': landing ? 'text/html; charset=utf-8' : windows ? 'text/plain; charset=utf-8' : 'application/x-sh; charset=utf-8',
        'Cache-Control': 'no-store',
        'X-Content-Type-Options': 'nosniff',
      },
    });
  },
};
