---
title: API
description: "The OpenAI-compatible /v1 endpoint: keys, supported calls, what the journal records, context window."
---

Yorath serves its local model to any client compatible with OpenAI Chat Completions: the `openai`
Python SDK, Continue, Open WebUI, curl. Ollama is never exposed — every call goes through the Gateway.

## Connect a client

1. In the web panel, open API keys and create a key. It is shown once; the database stores only its
   hash.
2. The endpoint is `https://<panel address>/v1`, the model is `local/default`.

```sh
curl https://<address>/v1/chat/completions \
  -H "Authorization: Bearer yorath_key_…" \
  -H "Content-Type: application/json" \
  -d '{"model": "local/default", "messages": [{"role": "user", "content": "Hello"}]}'
```

```python
from openai import OpenAI

client = OpenAI(base_url="https://<address>/v1", api_key="yorath_key_…")
for chunk in client.chat.completions.create(
    model="local/default", stream=True, messages=[{"role": "user", "content": "Hello"}]
):
    print(chunk.choices[0].delta.content or "", end="")
```

Supported: `GET /v1/models`, `POST /v1/chat/completions` and `POST /v1/responses` (the Responses
API, needed by Codex), streaming or not, including `tools` of type `function`: the client runs the
tools itself, and Yorath sees only their names. Both formats produce the same `model-api` execution
with the same checks and a journal without text.


An agent answers on the same endpoint with a key narrowed to it: [curl and Telegram](../agents/curl-and-telegram.md#agent-over-an-api-key).

## Your API key and how to keep it safe

A key is created on the API keys screen and shown once. It gives access to the Yorath model on your
behalf.

- Don't paste the key into chats, emails or shared files. If it ended up in a conversation, revoke it on
  the same screen and create a new one.
- Keep the key in an environment variable (`export YORATH_API_KEY=...`), not in a config file.
- A regular key gives **only the model** (`local/default`), not your agents: to talk to an agent, use
  an agent key or sign in with a login — see [curl and Telegram](../agents/curl-and-telegram.md#message-your-agent-from-the-terminal).

## What the journal shows

Every request is an execution of the built-in `model-api` agent (`hosting: external`, control level
`gateway`) on behalf of the key owner. The journal records the model, tokens, time, result and denials.
The request and response text appears in neither the trace nor the audit.

Only the local model is available through the endpoint: a request for `openrouter/default` gets 403
`model_not_allowed`, the provider isn't called, and the denial is visible in the journal.

A revoked key and the key of a disabled user get 401. The owner sees and can revoke every user's keys.

## Context window

Each model has its own window (`context_length`, shown on the Models screen): 32,768 on a node with a
GPU, 4,096 on CPU. Clients with long requests (Codex, IDE agents) get a truncated context with the CPU
window.
