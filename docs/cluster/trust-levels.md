---
title: Trust levels of servers
description: "Limit a less trusted server (a rented GPU, another site) to some data classes, so sensitive data never reaches it."
---

By default a node may receive data of every class. If one server is less trusted than the others — a GPU rented in a cloud, a machine at another site — narrow the classes it may receive:

```sh
yorath node set-classes node-3 public internal
```

From then on:

- a model that accepts more classes than the node allows **can't be placed** on it: `yorath model pull` refuses with `node_class_denied`;
- so a request with Confidential data never goes to a model on that node — it stays on servers you trust.

Narrow the classes before you place models there. If the node already holds a model that accepts more
classes, `set-classes` refuses with `placements_block` and names the model: take it off first
(`yorath model remove <name> --node node-3`), narrow the classes, then place a model whose classes fit.

Example: confidential requests stay on the company's own servers; public and internal ones may also use the rented GPU.

It worked if placing a model that accepts Confidential data on that node is refused with `node_class_denied`.

The classes themselves: [Data classes](../data-access/data-classes.md). Placing models: [Models on cluster nodes](../models/on-cluster-nodes.md).
