import { useEffect } from 'react';

/** Route API anchors even when Settings is already open on another category. */
export function useOpenApiDeepLink(openSettingsTab) {
  useEffect(() => {
    const handleHash = () => {
      const hash = window.location.hash;
      if (
        hash &&
        (hash.startsWith('#tag/') || hash.includes('speech-platform') || hash.includes('openapi'))
      ) {
        openSettingsTab('openapi');
      }
    };
    handleHash();
    window.addEventListener('hashchange', handleHash);
    return () => window.removeEventListener('hashchange', handleHash);
  }, [openSettingsTab]);
}
