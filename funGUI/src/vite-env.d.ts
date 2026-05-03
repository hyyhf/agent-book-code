/// <reference types="vite/client" />

interface Window {
  funharness?: {
    apiBase: string;
    isElectron?: boolean;
    platform?: NodeJS.Platform;
    showMenu?: (menuName: string) => Promise<void>;
  };
}
