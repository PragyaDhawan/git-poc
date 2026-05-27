# GitHub Webhook Receiver

A FastAPI app that receives GitHub webhook events and responds back via the GitHub API.

## Features

| Event | Actions handled | Bot response |
|---|---|---|
| `ping` | — | Responds with "pong" |
| `pull_request` | `opened` | Welcome comment + `needs-review` label + pending status |
| `pull_request` | `synchronize` | New-commits comment + pending status |
| `pull_request` | `closed` | Merge congratulation or closure note |
| `pull_request` | `review_requested` | Notifies the reviewer |
| `push` | — | Logs the push (easy to extend) |

---

## Quick start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env — add your GitHub token and webhook secret

# 3. Run the server
uvicorn app.main:app --reload --port 8000
```

The webhook endpoint is: `POST http://localhost:8000/webhook`

---

## GitHub token scopes

Create a token at **Settings → Developer settings → Personal access tokens** with:
- `repo` (full repository access)
- `write:discussion` (to post comments)

---

## Registering the webhook on GitHub

1. Go to your repo → **Settings → Webhooks → Add webhook**
2. **Payload URL**: your public URL (use [ngrok](https://ngrok.com) for local dev — see below)
3. **Content type**: `application/json`
4. **Secret**: the value of `WEBHOOK_SECRET` in your `.env`
5. **Events**: choose _"Let me select individual events"_ → tick **Pull requests** and **Pushes**

---

## Local development with ngrok

```bash
# In one terminal — start the app
uvicorn app.main:app --reload --port 8000

# In another terminal — expose it publicly
ngrok http 8000
# Copy the https://xxxx.ngrok.io URL → paste into GitHub webhook Payload URL
```

---

## Running tests

```bash
pytest tests/ -v
```

---

## Project layout

```
github-webhook/
├── app/
│   ├── main.py          # FastAPI app + webhook endpoint
│   ├── config.py        # Settings (loaded from .env)
│   ├── github_client.py # GitHub REST API calls
│   └── handlers.py      # Per-event business logic
├── tests/
│   └── test_webhook.py
├── requirements.txt
└── .env.example
```

---

## Adding a new event

1. Add a new `case` in `app/main.py` matching the GitHub event name.
2. Write a handler in `app/handlers.py`.
3. Add the event to the webhook subscription on GitHub.
