declare module '*config/diagnostics.json' {
  const schema: Record<string, unknown>;
  export default schema;
}

declare module '*config/analytics.json' {
  const destination: Record<string, unknown>;
  export default destination;
}

declare const __SUPERMETRY_APP_CONFIG__: {
  app_id?: string;
  ingest_key?: string;
  host?: string;
  diagnostics?: { sessionPings?: boolean };
};
