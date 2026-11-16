---
title: Add or replace a model
description: "Change the local model that answers agents, pick a model that will work, and pick up a new GPU."
---

Agents answer with the local model `local/default`. On a single server it is the one model in the
main node's Ollama; in a cluster it points at one of the placed models — see
[Models on cluster nodes](../models/on-cluster-nodes.md). You can change it in the web panel or with
a command on the server, which doesn't need the owner password. In the panel: Models → "Install a
model". Pick the model server in "Install on" (the main node's, or a GPU of a worker node; a free GPU
is picked first), then "Install" next to a model, or type another name. "Make it the default model"
points `local/default` at it. Nothing is pulled to the main node unless its server is picked.

```sh
yorath model                     # current model, context window, which one is recommended for the server
yorath model list                # catalog: memory needed, whether it fits, what is pulled
yorath model pull                # pull the recommended one for this server
yorath model pull qwen3.5:9b     # pull a specific one
```

Example on a server with an A10:

```
Server: 18 vCPU, 31 GB RAM, NVIDIA A10, 22 GB video memory; context window 32768
Main model (local/default): qwen3.5:9b, window 32768
Document search (local/embeddings): embeddinggemma-2:570m
Recommended for this server: qwen3.5:9b
```

**Which model will do.** Any model from the Ollama catalog or a GGUF from Hugging Face, if:

1. **it can call tools.** Without that, agents can't search documents or the web, and Codex can't edit
   files. On [ollama.com](https://ollama.com/search?c=tools) such models have the `tools` tag.
   "Uncensored" models (`abliterated`, `uncensored`) often lose this ability — `yorath model pull`
   warns you;
2. **it fits in memory**: video memory on a GPU, RAM on a CPU. `yorath model list` calculates this for
   catalog models; for other models it's an estimate;
3. **Yorath sets the context window**, not the model: 32,768 on a server with a GPU, 4,096 without one.

A model from Hugging Face: `yorath model pull hf.co/<author>/<repository>:<quantization>`.

If it doesn't fit by estimate, the command refuses; if you're sure: `yorath model pull <name> --force`.
The previous model is unloaded from memory; to get it back, run `yorath model pull <previous name>`.
Every model change is recorded in the journal (`model.registered`: who, from which model to which).

## Added a GPU or memory

If you added a GPU or memory after installing:

1. make sure the system sees the GPU: `nvidia-smi` (after installing the driver, a server reboot is
   often needed);
2. run the installer again: `sh /opt/yorath/installer/install.sh`. On every run it detects the GPU
   again, connects it to Ollama and prints "hardware changed … after the install check the model:
   yorath model";
3. change the model: `yorath model` should show the GPU, then run `yorath model pull`.
