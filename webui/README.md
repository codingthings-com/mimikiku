# Mimikiku web UI

The browser app for Mimikiku: record from the mic, watch the live transcript, and browse, edit and export transcripts. Admins also manage users and service settings here.

Built with Svelte 5 and Vite. The server image builds it, and FastAPI serves the result from `dist/`.

## Development

You need Node.js and a running Mimikiku server.

```bash
npm install
npm run dev                                       # http://localhost:5173
MIMIKIKU_SERVER=http://gpu-box:7777 npm run dev   # use another server
```

In dev mode, Vite forwards `/listen` (WebSocket) and `/api` to the server. That is `http://localhost:7777` unless you set `MIMIKIKU_SERVER`. Add `-- --host 0.0.0.0` to open the dev server from other devices.

```bash
npm run build     # production build into dist/
npm run preview   # serve the build locally
```

## Layout

```
src/
  App.svelte       routes and navigation
  routes/          one file per page
  lib/             shared components and state
    api.js           fetch wrapper (a 401 goes to sign-in)
    auth.svelte.js   the signed-in user
    session.svelte.js  the open transcript and the recorder
```

## Pages

| Route | Page |
|---|---|
| `/` | Home |
| `/new`, `/t/:id` | Record and view a transcript |
| `/transcripts` | All your transcripts |
| `/account` | Password and API tokens |
| `/admin/users` | Users (admin) |
| `/admin/service` | Service settings (admin) |
| `/monitoring` | Model status and performance (admin) |

Routing is hash-based (`/#/t/<id>`). The only things kept in the browser are the mic choice and the monitoring refresh settings.
