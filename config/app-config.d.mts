export const PRODUCT_NAME: string;
export const APP_ID: string;
export const DEFAULT_BACKEND_PORT: number;
export const DEFAULT_WEB_PORT: number;
export const DEFAULT_ELECTRON_DEV_PORT: number;
export const DEFAULT_ELECTRON_PROXY_PORT: number;
export const APP_CONFIG: Readonly<{
  productName: string;
  appId: string;
  ports: Readonly<{ backend: number; web: number; electronDev: number }>;
}>;
