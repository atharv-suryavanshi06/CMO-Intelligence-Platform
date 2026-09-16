# Presenton deployment

The CMO API connects to a server-side Presenton deployment for editable PPTX
generation. Presenton owns the Gemini provider credentials; the browser only
talks to the CMO API.

## Recommended local Docker deployment

```powershell
docker run -it --name presenton -p 5001:80 `
  -e LLM=google `
  -e GOOGLE_API_KEY=your-gemini-key `
  -e GOOGLE_MODEL=models/gemini-2.0-flash `
  -e WEB_GROUNDING=false `
  -e CAN_CHANGE_KEYS=false `
  -v "${PWD}\presenton-data:/app_data" `
  ghcr.io/presenton/presenton:latest
```

The CMO API should be configured separately:

```text
PRESENTON_BASE_URL=http://127.0.0.1:5001
PRESENTON_API_KEY=sk-presenton-...
PRESENTON_TIMEOUT_SECONDS=120
```

Keep `GOOGLE_API_KEY` and other provider credentials inside the Presenton
deployment. Do not put them in the frontend or in the CMO API's browser-facing
configuration.

## Runtime contract

The CMO API uses Presenton's v1 asynchronous generation endpoints:

- `GET /api/v1/ppt/template/all` to populate the template selector.
- `POST /api/v1/ppt/presentation/generate/async` with `export_as: "pptx"` to
  create a task, then `GET /api/v1/ppt/presentation/status/{task_id}` to poll
  its state.
- A relative path, configured-origin URL, or signed public HTTPS artifact URL
  is downloaded by the CMO API and proxied as binary PPTX bytes to the
  authenticated browser. External artifact downloads do not receive the
  Presenton API key. When the configured Presenton origin is local, self-hosted
  container-network paths are rebuilt on it; cloud configurations reject local
  and non-HTTPS targets.
- `POST /api/v1/ppt/presentation/export` with `export_as: "pdf"` creates the
  local in-app preview of a completed deck.

Generation always sends `web_search: false`, professional tone, and strict
instructions that allow only facts already present in the selected chat
context. Generic non-factual visuals and icons remain allowed. The CMO API
does not retry generation automatically.

Presenton template IDs are loaded dynamically, so custom templates can be
added to the Presenton deployment without changing the CMO API contract.
