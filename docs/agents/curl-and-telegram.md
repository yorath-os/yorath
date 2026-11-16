---
title: curl and Telegram
description: "Talk to your agent from the terminal, or give a single agent its own API key for a bot or an integration."
---

## Message your agent from the terminal

The easiest way is a key narrowed to the agent: see the
[next section](#agent-over-an-api-key). Without a key, you reach an agent
the same way the web panel does: sign in with a login and password, open a conversation, send a message.

```sh
A=https://<IP>/api        # on the server itself you can use http://127.0.0.1:8000
C=~/.yorath-cookies
J='Content-Type: application/json'

# 1. sign in: the password is prompted for and stays out of the shell history
read -s -p 'Password: ' P; echo
printf '{"login":"<login>","password":"%s"}' "$P" | \
  curl -s --cacert ~/yorath-root-ca.crt -c $C -H "$J" -d @- $A/auth/login; echo; unset P

# 2. the agent's name (the "name" field)
curl -s --cacert ~/yorath-root-ca.crt -b $C $A/chat/agents

# 3. a conversation with the agent — note its "id"
curl -s --cacert ~/yorath-root-ca.crt -b $C -H "$J" -d '{"agent":"<name>"}' $A/chat/conversations

# 4. a message; the answer streams as token … done events
curl -sN --cacert ~/yorath-root-ca.crt -b $C -H "$J" -d '{"content":"Hello"}' \
  $A/chat/conversations/<id>/messages
```

Replace `<login>` and `<name>` with your own and drop the angle brackets: signing in as `<login>`
returns `invalid_credentials`. Every message is an agent run in the Journal on your behalf.

## Agent over an API key

An API key can be issued for a single agent. Such a key is answered by the agent with its own
instructions, tools and data; it opens neither the bare model nor other agents.

1. Agents → your agent → "Edit" → check "Can be called with an API key" → "Save".
2. API keys → "Access": "agent <name>" → "Create key". Copy the key and the request example.
3. Call it like the model, but with `model` = `agent/<name>`:

```sh
curl --cacert ~/yorath-root-ca.crt https://<IP>/v1/chat/completions \
  -H "Authorization: Bearer $YORATH_API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "agent/<name>", "messages": [{"role": "user", "content": "Hello"}]}'
```

The client sends the conversation history itself in `messages` (a bot keeps the last messages of each
chat). Every call is an agent run in the Journal on behalf of the key owner. The key sees the same data
you do, so for a bot it's better to create a separate user and create the key as that user.
A Telegram bot example is [below](#a-telegram-bot).

Rules:

- The protocol is stateless: the client sends the conversation history itself in `messages`. The last
  message must be from `user`, otherwise 400. The client's `system` and `tool` messages and its `tools`
  field don't reach the prompt: instructions and tools come from the agent's card, and so does the model.
- Every call is an execution of the agent with the `api` trigger on behalf of the key owner: tools, PDP
  checks, data class, outcome — the same as in chat. Unlike `model-api`, this is an agent, and its trace
  is written like an agent's in chat. No conversation is created in the panel.
- An agent key opens nothing else: `local/default`, other agents and `/v1/responses` return
  403 `model_not_allowed`; `/v1/models` returns only `agent/<name>`. A regular key can't call agents. If
  the agent is disabled or its `api` trigger removed — 403 without a model call.
- The key sees the same data as its owner (∩ the agent's data). For a bot, create a separate user and
  issue the key on their behalf.
- The response carries a `yorath` extension: `execution_id` (to find the call in the journal) and
  `citations`.


## A Telegram bot

A Telegram bot sketch (python-telegram-bot and openai):

```python
import os

from openai import OpenAI

client = OpenAI(base_url="https://<address>/v1", api_key=os.environ["YORATH_API_KEY"])
history: dict[int, list[dict]] = {}

async def on_message(update, context):
    chat = history.setdefault(update.effective_chat.id, [])[-10:]
    chat.append({"role": "user", "content": update.message.text})
    answer = client.chat.completions.create(model="agent/sales-bot", messages=chat)
    text = answer.choices[0].message.content
    chat.append({"role": "assistant", "content": text})
    history[update.effective_chat.id] = chat
    await update.message.reply_text(text)
```
