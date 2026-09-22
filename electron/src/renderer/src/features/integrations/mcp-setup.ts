export const MCP_CLIENTS = {
  'claude-code': {
    file: '.mcp.json',
    docs: 'https://code.claude.com/docs/en/mcp',
    format: 'json',
    type: 'http',
  },
  cursor: {
    file: '.cursor/mcp.json',
    docs: 'https://cursor.com/docs/mcp',
    format: 'json',
    type: undefined,
  },
  // Codex reads `[mcp_servers.<name>]` tables from ~/.codex/config.toml; a
  // `url` key selects Streamable HTTP and `http_headers` adds static headers.
  // https://developers.openai.com/codex/mcp
  'codex-cli': {
    file: '~/.codex/config.toml',
    docs: 'https://developers.openai.com/codex/mcp',
    format: 'toml',
    type: undefined,
  },
} as const;

export const MCP_CLIENT_ID_HEADER = 'X-OmniVoice-Client-Id';
/** The trailing-slash form works on every backend version; bare `/mcp` only on
 * backends that serve it directly (it used to 405 behind the SPA mount). */
export const MCP_ENDPOINT = '/mcp/';

/** Export configuration, never credentials or commands that overwrite client files. */
export function mcpSetup(slug: string, baseUrl: string) {
  if (!Object.hasOwn(MCP_CLIENTS, slug)) return null;
  const client = MCP_CLIENTS[slug as keyof typeof MCP_CLIENTS];
  const url = backendEndpoint(baseUrl, MCP_ENDPOINT);
  if (!url) return null;
  if (client.format === 'toml') {
    return {
      file: client.file,
      docs: client.docs,
      format: client.format,
      // JSON string syntax is valid TOML basic-string syntax, so values stay escaped.
      text: [
        '[mcp_servers.voicestudio]',
        `url = ${JSON.stringify(url)}`,
        `http_headers = { ${JSON.stringify(MCP_CLIENT_ID_HEADER)} = ${JSON.stringify(slug)} }`,
        '',
      ].join('\n'),
    };
  }
  return {
    file: client.file,
    docs: client.docs,
    format: client.format,
    text: JSON.stringify(
      {
        mcpServers: {
          voicestudio: {
            ...(client.type ? { type: client.type } : {}),
            url,
            headers: { [MCP_CLIENT_ID_HEADER]: slug },
          },
        },
      },
      null,
      2,
    ),
  };
}

/** Validate the configured backend without exporting URL credentials. */
export function backendEndpoint(baseUrl: string, endpoint: string) {
  try {
    const base = new URL(baseUrl);
    if (
      !['http:', 'https:'].includes(base.protocol) ||
      base.username ||
      base.password ||
      base.search ||
      base.hash
    )
      return null;
    return `${base.href.replace(/\/+$/, '')}${endpoint}`;
  } catch {
    return null;
  }
}
