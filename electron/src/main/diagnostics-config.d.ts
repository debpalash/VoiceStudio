declare module '*config/diagnostics.json' {
  const schema: Record<string, unknown>;
  export default schema;
}

declare module '*config/analytics.json' {
  const destination: Record<string, unknown>;
  export default destination;
}
