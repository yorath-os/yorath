---
name: openclaw
description: "OpenClaw personal agent: channels (Telegram), memory, tools; Yorath installs it as a container"
runtime: openclaw
hosting: managed
model: local/default
models: [local/default]
mode: autonomous
tools: []
data: []
max_data_class: internal
triggers: [chat, api]
---
Connected agent OpenClaw (decision 34). It runs in its own container on a node and gets the Yorath
model with its own key; requests to it from the chat and through `/v1` (`agent/openclaw`) are passed
by Yorath to the agent's entry point.

Control level `gateway`: Yorath sees model calls, requests to the agent and its network (domains),
and can turn off the agent and its internet access, but it does not see or limit the agent's own tools
(terminal, files in its container). This text is not passed to the agent: it has its own instructions.
