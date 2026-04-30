/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GROVE_API?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
