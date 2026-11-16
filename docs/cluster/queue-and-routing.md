---
title: Queue and routing
description: "Which server answers a request, and what happens when every server of a model is busy."
---

A request to a catalog model goes to the main node's Gateway, which picks a model server that holds the model. How models get onto servers: [Models on cluster nodes](../models/on-cluster-nodes.md).

## When every server of a model is busy

A request goes to a model server only when it is free, so it
starts at once. The rest wait on the main node, in the queue of their model, and take the first server
that frees:

- the queue is served in turn between users (and agents): a burst of requests from one client doesn't
  hold everybody else; requests of one user go in the order they came;
- the chat says "Waiting for the model" with the number of requests ahead; in Tasks such a run is
  `queued`; Models → "Model servers" shows how many requests wait for each model;
- a request that waited more than 120 seconds gets `queue_timeout` (HTTP 503); when 100 requests already
  wait for the model, a new one gets `queue_full` (HTTP 429) at once. Both limits are in
  `distribution.yaml` (`gateway.model_queue_wait_seconds`, `gateway.model_queue_max`);
- when several servers are free, a request goes to the one that served the same user last: the model
  doesn't re-read a long conversation there (0.3 s against 10 s on a 28,000-token history);
- if a node stops answering, its servers are left out until the node reports again, and a request that
  got no answer yet is repeated once on another server of the same model.

The queue lives in the core's memory: restarting the core drops the waiting requests (the clients get
an error and repeat).

Not yet: priorities, several chat models on one card, one model spread over two cards, a model server
per card on the main node (the main node's Ollama gets all its cards), the queue length in
`yorath model list` (it is in the panel).
