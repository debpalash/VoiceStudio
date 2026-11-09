import { createContext } from 'react';

/**
 * True where the first-run model store is on the same page. The store already
 * lists every model with its install actions, so family lists below it skip
 * model rows that offer nothing else (no selection) instead of repeating them.
 */
export const ModelStoreShownContext = createContext(false);
