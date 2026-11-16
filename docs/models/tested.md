---
title: Tested models and hardware
description: "Measured answer times on CPU and GPU servers, and which local models passed Codex coding tasks."
---

Every number on this page comes from a measured run on a clean server. The defaults Yorath picks follow from them: `gemma4:e2b` without a GPU, `qwen3.5:9b` with one.

## Chat answer from documents, CPU only

The same 20 questions over a document source; a context of about 1,000 tokens.

| Server | Model | Generation, tokens/s | First token | Full answer (median) |
|---|---|---|---|---|
| 4 vCPU / 8 GB | gemma4:e2b | 10.3 | 13.7 s | 18.9 s |
| 4 vCPU / 8 GB | qwen3.5:4b | 6.0 | 25.4 s | 42.6 s |
| 8 vCPU / 8 GB | gemma4:e2b | 12.2 | 7.6 s | 11.7 s |
| 16 vCPU / 16 GB | gemma4:e2b | 13.1 | 4.3 s | 8.0 s |
| 16 vCPU / 16 GB | qwen3.5:9b | 7.8 | 11.7 s | 18.7 s |

The first token depends on the number of cores (the prompt is processed on the CPU), so cores matter more than memory. 4 vCPU with AVX2 / 8 GB is the minimum; 16 vCPU / 16 GB is recommended for CPU-only. Below the minimum the wizard offers only an external model via OpenRouter.

## Chat answer, GPU

NVIDIA A10 (24 GB): an answer from documents in 0.4–1.5 s with `qwen3.5:9b` and a 32,768-token window.

## Answer quality, 20 questions

| Model | Memory | Correct / partial / wrong |
|---|---|---|
| qwen3.5:9b | 10.3 GB | 20 / 0 / 0 |
| qwen3.5:4b | 6.9 GB | 16 / 4 / 0 |
| gemma4:e2b | 7.3 GB | 15 / 4 / 1 |

## Codex CLI on the Yorath model

NVIDIA A10, window 32,768; five coding tasks (from creating a file to a change across three files with hidden tests), three runs each.

| Model | Solved | Median per task |
|---|---|---|
| qwen3.6:27b | 13–14 / 15 | 32–35 s |
| qwen3.5:9b | 13 / 15 | 14.5 s |
| qwen3.5:4b | 11 / 15 | 11.9 s |
| qwen3-coder:30b | 9 / 15 | 7.8 s |
| gpt-oss:20b | 5 / 15 | 38.3 s |
| any model, window 4,096 (CPU) | 0 / 4 | — |

With a 4,096-token window Codex doesn't work at all: its first request is about 12,700 tokens and the model never sees the task. That's why Codex and the connected agents need a GPU.

## Cluster: two cards behind one endpoint

Main node without a GPU, a worker node with 2× A10, `qwen3.5:9b` on both cards; requests of 400 tokens (about 7 s each). Four requests from one user at once: two run, two wait in the main node's queue; all four done in 14.0 s. A second user's request sent during a burst of eight from the first one is served in the second round (13.7 s), not after the burst (31.8 s).
