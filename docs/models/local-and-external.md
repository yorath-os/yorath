---
title: Local and external models
description: "The local model is the default; an external model through OpenRouter is used only for the data classes it is allowed."
---

Yorath answers with a **local model** by default: it runs in Ollama on your server, and nothing leaves the server. The setup wizard picks it for your hardware — `gemma4:e2b` without a GPU, `qwen3.5:9b` with one — see [Tested models and hardware](tested.md).

An **external model** is optional. Add an OpenRouter key on the wizard's "External services" step and confirm that you agree to send data to it. Then agents whose file allows `openrouter/default` can use it — the Developer Agent uses it by default.

## What may go to an external model

A model is a resource with allowed data classes. The external model accepts only **Public** and **Internal** data by default. If a conversation holds Confidential data (for example, the agent read the vacation table), a call to the external model is **blocked with a reason** — Yorath never quietly answers with another model. The denial is in Journal → Denials.

| | Local | External (OpenRouter) |
|---|---|---|
| Where it runs | your server | the provider's servers |
| Data classes | all | Public, Internal |
| Cost | your hardware | per token, shown on the Desk |
| Needs | 8 GB RAM; a GPU for long contexts | a key and your consent |

Some countries are blocked by OpenRouter; see [Web search behind a proxy](../operate/web-search-behind-a-proxy.md).

To change the local model: [Add or replace a model](add-or-replace.md).
