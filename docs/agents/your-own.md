---
title: Your own agent
description: "Create an agent from a built-in one in the panel: one Markdown file with its model, tools, data and instructions."
---

You need: Yorath installed and signed in as the owner.

1. Open Agents → All agents and pick the built-in agent closest to what you need — for example `document-assistant` for answers from your documents.
2. Click "Create from this". The form opens with that agent's settings.
3. Give it a Name and a Description, write the Instruction, and tick only the Data, Tools and Models the agent really needs.
4. Click "Save". The agent appears in the list and in Chat.

Yorath keeps the agent as one Markdown file — the header holds what you ticked, the text is the instruction. The keys are in [Agent file format](../reference/agent-file-format.md).

It worked if the new agent answers in Chat, and Journal shows its run with its own name, the model call and the access checks.

If it didn't: the form shows why it was not saved (for example, a name that is already taken).

To reach the agent from a script or a bot, tick "Can be called with an API key" under External calls and create an agent key — see [curl and Telegram](curl-and-telegram.md#agent-over-an-api-key).
