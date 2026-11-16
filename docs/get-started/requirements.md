---
title: Requirements
description: "Server, ports and hardware Yorath needs, and what changes with a GPU."
sidebar:
  order: 2
---

## Server

- A clean **Ubuntu 24.04 LTS, x86_64** server with `root` SSH access.
- At least **4 vCPU (with AVX2) / 8 GB RAM / 40 GB disk**. Comfortable: 8–16 vCPU / 16 GB. A GPU is optional.
- Open inbound ports **22, 80, 443**.
- A computer with a browser and an SSH client.
- Optional: an [OpenRouter](https://openrouter.ai/keys) key, if you want external models in addition to the local one.

## What a GPU changes

Without a GPU Yorath runs a small local model with a 4,096-token context window: chat with documents, the built-in agents and the API work. These need a GPU (a 32,768–65,536 window):

- [Codex CLI](../agents/codex-cli.md) on the Yorath model: its first request is about 12,700 tokens;
- [OpenClaw and Hermes](../agents/openclaw-and-hermes.md): 14–27 thousand tokens even for a simple question; on CPU Yorath refuses with an explanation.

## Measured speed

Measured: at least 4 vCPU (AVX2) / 8 GB (an answer from documents in ~19 s), acceptable at 8 vCPU (~12 s), recommended 16 vCPU / 16 GB (~8 s), a 24 GB GPU — 0.4–1.5 s. Below the minimum, the local model is replaced by an external one via OpenRouter.

Time to start: about 5 minutes with an NVIDIA A10 (18 vCPU / 31 GB: install 3.5 min, first agent answer 1.5 min later) and about 7.5 minutes on the minimum 4 vCPU / 8 GB without a GPU (install about 4 min, first answer 3.5 min later). Measured on 15.11.2026 on clean Ubuntu 24.04 servers, from the install command to the first agent answer, model download included and the time you spend in the setup wizard not.

More numbers: [Tested models and hardware](../models/tested.md).
