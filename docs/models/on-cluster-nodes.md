---
title: Models on cluster nodes
description: "Place models on the GPUs of worker nodes, point local/default at one, and keep the main node light."
---

The main node can be a small server without a GPU while the models run on the nodes. Three terms:

- a **model server** is one Ollama: the main node has one (`local/ollama`; the main node is called `local` unless renamed), a worker node has one per
  GPU (`node-2/gpu0`, `node-2/gpu1`), each seeing only its own card. A node without a GPU runs one
  (`cpu`) only if it was joined with `YORATH_NODE_MODEL=on`;
- a **placement** is a model on a model server. One chat model stands on a server. The same model on
  several servers answers several requests at once;
- the **catalog** is the list of placed models, each named `local/<Ollama name>`
  (`local/qwen3.5:9b`). `local/default` is a pointer at one of them.

Everything is done on the main node; it pulls the model into the node's Ollama through the tunnel.

```sh
yorath model list                                   # the catalog, placements and every model server
yorath model pull qwen3.5:9b --node node-2          # on every free card of node-2; pulled once
yorath model pull qwen3.5:9b --node node-2 --gpu 1  # only on card 1
yorath model pull gemma4:e2b --node node-2 --gpu 0 --replace   # swap the model that stands there
yorath model default qwen3.5:9b                     # point local/default at it
yorath model remove gemma4:e2b                      # take it off the main node (--node for a worker)
```

The same is in the panel: Models → "Install a model" (pick the server in "Install on"; the table shows
the memory, whether the model fits and the window on that server, ★ is the one recommended for it) and
"Model servers" (take a model off, make one the default). A free GPU without a model gets an offer
with the recommended model. If the same model already runs elsewhere with another window
(`window_mismatch`), "Move … to …" takes it off those servers and installs it on the picked one.

Example: the main node has no GPU, `node-2` has two cards.

```
  CATALOG MODEL                  WINDOW   PLACEMENTS
* local/qwen3.5:9b               65536    node-2/gpu0, node-2/gpu1

  MODEL SERVER           GPU                    MODEL
  local/ollama           no GPU                 free
  node-2/gpu0            NVIDIA A10 23 GB       local/qwen3.5:9b
  node-2/gpu1            NVIDIA A10 23 GB       local/qwen3.5:9b
```

**Which model answers.** An agent or a client names a model. `local/default` is whatever the pointer
says; a catalog name (`local/qwen3.5:9b`) is that model and nothing else. Through `/v1` every catalog
model is available by name; an agent calls a catalog model if its file lists it (`models: [local/default,
"local/*"]` allows all of them). The journal records both the requested name and the model and server
that actually answered.

**When a model is unavailable.** If none of its placements is ready on a running node, the request
gets `model_unavailable` with the reason (`node node-2 is lost`). Yorath never answers with another
model instead.

**When the default model's node is lost.** If no server of the model behind `local/default` has
answered for two minutes, `local/default` moves to a local model that is up: one on a GPU first, then
the one with the larger context window. It never moves to an external model. The Desk and the Models
window say what moved where and why, and which agents the new window doesn't fit (Hermes and OpenClaw
need 65,536 tokens); the switch is in the audit as `model.default.switched`. Agents with a model chosen
on their card are not switched. When the node is back, the warning says so and offers "Return default
to …"; Yorath doesn't move the default back by itself.

**The main node without a chat model.** Place the model on a node, point `local/default` at it, then
`yorath model remove <old model>`: the main node keeps the panel, the checks, the journal and the
document search model, and gives its memory away. Removing the last placement of the default model
asks for `--force`.

**Rules a placement must pass** (the command refuses with the reason before pulling anything):

| Refusal | Why | What to do |
|---|---|---|
| `server_busy` | another chat model stands on the server | pick another card or add `--replace` |
| `window_mismatch` | the model already runs with a different context window (a GPU gives 65,536, a CPU 4,096) | place it on servers of the same kind |
| `does_not_fit` | by estimate the model doesn't fit one card's memory | a smaller model, or `--force` |
| `node_class_denied` | the node doesn't allow a data class the model accepts | see below |

**Data classes of a node.** By default a node may receive data of every class. If a node is less
trusted than the others (a rented GPU, another site), narrow it; a model that accepts more classes
can't be placed there, so such data never leaves for that node:

```sh
yorath node set-classes node-3 public internal
```

**A node joined by an older version, or a GPU added later.** Put the new version into `/opt/yorath` on
the node and run `sh /opt/yorath/installer/install.sh` there (no `--join`): it installs the GPU driver
for containers, starts a model server per card and reports the cards to the main node.


When several requests arrive at once, see [Queue and routing](../cluster/queue-and-routing.md).
