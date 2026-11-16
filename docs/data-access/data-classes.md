---
title: Data classes
description: "Public, Internal, Confidential, Restricted: how a class is set on data, models, agents and servers, and what it blocks."
---

Every data source, every model, every agent and every server has a data class. The class says how sensitive data is, and Yorath compares classes before anything is sent.

| Class | Meaning | Example |
|---|---|---|
| Public | may be published | product docs, web search results |
| Internal | for employees | team policies |
| Confidential | for a limited group | salaries, vacation balances |
| Restricted | the most sensitive | personal data under regulation |

## Where a class is set

| On | What it means |
|---|---|
| A data source (Data screen) | the class of everything read from it |
| A model | the highest class the model may receive: the local model accepts all, an external model through OpenRouter only Public and Internal by default |
| An agent (`max_data_class` in its file) | the highest class the agent may read |
| A tool that sends data out (`web_search`) | not called when the conversation already holds data above its class |
| A server in a cluster | the classes a model on that server may receive — [Trust levels of servers](../cluster/trust-levels.md) |

## The class follows the data

When an agent reads a Confidential document, the whole conversation becomes Confidential. From then on, a call to a model or a tool that doesn't accept Confidential is **blocked with a reason** — Yorath never quietly switches to another model. The denial is in Journal → Denials.

It worked if, after asking an agent about a Confidential document, a request that would go to an external model shows a denial in the Journal instead of an answer from that model.
