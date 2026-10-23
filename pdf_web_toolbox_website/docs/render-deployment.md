# Deploy PDF Web Toolbox to Render

The repository-root `/render.yaml` is the canonical Render Blueprint. It deploys the existing FastAPI application from `pdf_web_toolbox_website/backend` and serves the frontend bundled in `app/frontend_dist`. No separate static site or frontend rewrite is required.

## Create the Render service

1. Sign in at https://dashboard.render.com/ and connect the GitHub repository `lingump287-eng/pdf-web-toolbox`.
2. In Render, choose **New → Blueprint** (or create a Web Service from the repo if Blueprint is unavailable).
3. Select the `main` branch and the repository-root `render.yaml`.
4. Review the service before creating it. The Blueprint requests one Python Web Service named `pdf-web-toolbox`, initially on the free plan.
5. Wait for the first build/deploy. Open the assigned `onrender.com` URL and verify `/api/health` returns `{"status":"ok"}`.

The service uses Python 3.12.8, installs `backend/requirements.txt`, and runs:

```sh
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

The service uses `/tmp/pdf-web-toolbox` for temporary task files. It does not require a database or persistent volume for the current anonymous MVP.

## Required post-deploy smoke tests

- `GET /` returns the PDF Web Toolbox interface.
- `GET /api/health` returns HTTP 200 and `{"status":"ok"}`.
- `GET /api/config` returns the current upload limits.
- Merge two small sample PDFs, poll the returned job ID to completion, and download the output.
- Run a small PDF workflow, inspect the result summary/preview, and verify a ZIP output if split mode is selected.
- Scan a PDF with a blank page and verify the scan only suggests candidates; deletion must require explicit confirmation.

## Important free-plan limitations

- Free web services sleep after inactivity and take time to wake.
- The local filesystem is ephemeral. Temporary uploads and outputs are not durable.
- Job state is stored in process memory; a process restart/deploy can interrupt in-flight jobs and make prior result IDs unavailable.
- Keep one instance for this MVP. Do not enable multiple replicas for paid production until jobs, status and quota accounting use shared durable storage/queue.
- For reliable commercial use, upgrade to an always-on instance and design durable job storage, usage limits, monitoring, backups and support. Do not promise paid SLAs on the free plan.

## Safe cutover

Keep Railway intact during Render setup. Change any public-facing links only after the smoke tests above pass. The existing Railway deployment is the rollback option until the new URL is verified.
