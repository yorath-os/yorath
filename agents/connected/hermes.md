---
name: hermes
description: "Hermes Agent (Nous Research): memory, skills, channels (Telegram); Yorath installs it as a container"
runtime: hermes
hosting: managed
model: local/default
models: [local/default]
mode: autonomous
tools: []
data: []
max_data_class: internal
triggers: [chat, api]
---
Connected agent Hermes Agent (decision 34). It runs in its own container on a node and gets the Yorath
model with its own key; requests to it from the chat and through `/v1` (`agent/hermes`) are passed
by Yorath to its API server.

Control level `gateway`: Yorath sees model calls, requests to the agent and its network (domains),
and can turn off the agent and its internet access, but it does not see or limit the agent's own tools.
This text is not passed to the agent: it has its own instructions.
