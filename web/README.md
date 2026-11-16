# web

Yorath web UI (React + TypeScript + Vite). Currently a status page (`/api/health`, `/api/version`) and a setup wizard stub.

- `npm run dev` — dev server proxying `/api` → core (`VITE_API_PROXY`, default `http://localhost:8000`)
- `npm run build` — build into `dist/`; in production Caddy serves it (`installer/Dockerfile.web`, `installer/Caddyfile`)
- `npm run lint`, `npm run format`
