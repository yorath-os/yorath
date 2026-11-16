# Changelog

## [0.8.0](https://github.com/yorath-os/yorath/compare/v0.7.0...v0.8.0) (2026-11-16)


### Features

* **cluster:** move the default model off a lost node
* **core:** choose the model of any agent in its settings
* **core:** choose the models an API key can call
* **core:** restart a connected agent without losing its key or memory
* **web:** choose an agent's model on its card
* **web:** pick the models of an API key
* **web:** restart a connected agent from its card
* **web:** show the agent's model in the chat instead of a picker
* **web:** warn on the Desk when the default model moved by itself


### Bug fixes

* **cluster:** close the failover warning when the default is set from the server
* **core:** check the window of the agent's own model when enabling it
* **core:** pin the schema version to the key models migration
* **core:** say why web search fails instead of a bare HTTP 403
* **core:** shorten tool results to fit the model instead of dropping them
* **core:** take an external model's context window from its catalog
* **infra:** open site screenshots large on click
* **installer:** back up a running agent's memory despite its sockets
* **installer:** keep words of docker output out of container addresses
* **installer:** list every command in yorath help, node included
* **web:** enable an agent connected before without asking again
* **web:** give the command that puts the certificate next to an API call
* **web:** open an agent by a click anywhere on its row
* **web:** scroll only the conversation as an answer comes in

## [0.7.1](https://github.com/yorath-os/yorath/compare/v0.7.0...v0.7.1) (2026-11-16)


### Bug fixes

* **infra:** let a failed subscription email be retried at once
* **infra:** say "confirmed" when a subscription link is opened again
* **web:** give ready commands to connect an OpenClaw found outside Yorath
* **web:** list an outside agent connected by key apart from those to connect

## 0.7.0 (2026-11-16)


### Features

* **cluster:** see agents outside Yorath on every node
* **cluster:** tell what each model server gives a model
* **core:** check a connected agent's Telegram bot from its node
* **docs:** Agents, Security and Enterprise pages
* **docs:** home page of yorath.io
* **infra:** redirect www.yorath.io to yorath.io
* **infra:** service addresses of yorath.io
* **infra:** subscription with email confirmation and enterprise leads
* **installer:** install the published release with one command
* **web:** install a model on the model server you pick
* **web:** list agents found outside Yorath and connect them by key
* **web:** say on the agent card whether its Telegram bot works
* **web:** show how old each node's scan of outside agents is
* **web:** show how to reach a connected agent on its card
* **web:** the panel in the Yorath green with the Fork mark


### Bug fixes

* **cluster:** bound what a worker node may send to the main node
* **cluster:** let one join token add only one node
* **cluster:** move a model without taking it off its server first
* **core:** index documents that arrived before the embeddings model
* **core:** say "default" in yorath model list, not "main"
* **installer:** close metadata, private networks and direct DNS to agents
* **installer:** print a node's clone command ready to paste
* **installer:** re-run the installed copy's installer on an installed server
* **installer:** show the installed version without a marker comment
* **web:** ask the node when you enable a connected agent in a cluster
* **web:** enable model-api in a cluster without the connect window
* **web:** say why a connected agent cannot be enabled
* **web:** show agents found outside Yorath inside the agents window
* **web:** stop loading nodes once the panel is closed
