export interface SupermetryAppBuildConfig {
  app_id?: string;
  ingest_key?: string;
  host?: string;
  diagnostics: { sessionPings: boolean };
}
export function generatedSupermetryConfig(env?: Record<string, string | undefined>): SupermetryAppBuildConfig;
export function writeSupermetryConfig(env?: Record<string, string | undefined>, path?: string | URL): SupermetryAppBuildConfig;
