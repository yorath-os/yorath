---
name: model-api
description: API access to the local model for external clients with the user's key
runtime: native
hosting: external
model: local/default
models: [local/default, "local/*"]
mode: delegated
tools: []
data: []
max_data_class: restricted
triggers: [api]
---
Built-in external agent for the model endpoint (`/v1/chat/completions`, decision 32).

A client on the user's laptop (Python SDK `openai`, Continue, curl) sends its own messages
with the person's API key. Every request is an Execution of this agent on behalf of the key owner:
Gateway checks, token and cost accounting, and a journal entry without the request or response text.

This text is not added to client requests. The client runs tools itself; Yorath sees only
their names. By default only local models are available: `local/default` and, by name, every
local model of the catalog (`local/*`).
