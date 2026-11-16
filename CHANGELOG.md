# Changelog

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
