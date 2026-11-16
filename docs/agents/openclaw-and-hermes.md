---
title: OpenClaw and Hermes
description: "Install OpenClaw or Hermes from Yorath into its own container, on the Yorath model, with network control."
---

Yorath installs popular agents itself: Agents → "Connect agent", or a command on the server. The agent
runs in its own container, not as root, without access to Yorath's files or Docker; it gets the model
from Yorath with its own key and reaches the internet only through the node proxy.

```sh
yorath agent add openclaw                      # the version verified with this Yorath release
yorath agent add hermes --telegram             # with a bot: the token is prompted without echo
yorath agent list                              # connected or not, version, status, internet, bot
yorath agent logs openclaw                     # the agent container's log
```

**What you need.** Node egress control is on (`yorath egress on`): without it the agent would have
direct internet access. The context window of `local/default` is at least 65,536 tokens: both agents
send 14–27 thousand tokens to the model even for a simple question. On a server with a GPU this window
is the default; on CPU these agents are too slow, and Yorath refuses with an explanation.

**What Yorath sees.** Every model call by the agent is an execution of that agent in the journal
(level `gateway`), and so is every request to it from the Yorath chat or through `/v1`
(`model: agent/openclaw`). Journal → Network shows the domains the agent contacted. The agent's own
tools (terminal, files in its container) are neither visible to nor restricted by Yorath.

**How to use it.** The agent's card has a "How to use" block with three ways in:

- **Chat**: Chat → new conversation → pick the agent. Messages go to the agent on your behalf.
- **API**: "Create a key for openclaw" issues a key narrowed to this agent (shown once) and a ready
  `curl` call to `/v1/chat/completions` with `"model": "agent/openclaw"`. The key opens neither the
  bare model nor other agents; the client sends the conversation history itself. More in
  [curl and Telegram](curl-and-telegram.md#agent-over-an-api-key).
- **Telegram**: whether the bot works and whom it answers; the token and user ids go in the fields
  below the block. See [Is the bot working?](#is-the-bot-working) below.

**Model.** By default the agent uses `local/default`; pick another one under "Model" on its card or
with `yorath agent model hermes openrouter/default`. The model needs a context window of at least
65,536 tokens.

**Restart.** The "Restart" button or `yorath agent restart hermes`: the container is recreated with the
same key, bot, version and memory. Until the agent is up again, its card says "Restarting" and a
question gets "agent is restarting, retry in a minute".

**Disable and remove.** The switch on the agent's card, or `yorath agent remove openclaw`: the agent's
key is revoked, the container stopped, the memory kept; enabling it again issues a new key and brings
back the node, version, model, bot and its users, and the internet switch without asking again.
`yorath agent remove openclaw --purge` (the "Delete…" button) wipes the agent's memory and secrets too.

**Internet for the agent.** The "Turn off internet" button or `yorath agent internet openclaw off`: the
agent's new outbound HTTP and HTTPS connections are cut within a few seconds, and attempts show up in
the audit. The Yorath model stays available to the agent without internet.

**What else the agent can't reach.** Its container sees only the Yorath model (`core:8000` in its own
network) and the internet through the node proxy. The VPS metadata service (`169.254.169.254`, where
cloud-init keeps keys and user data), private networks of the provider, the cluster tunnel, other
services on the host and DNS servers of its own choosing are closed; names resolve through Docker as
usual. Attempts go to the kernel log of the node (`journalctl -k | grep yorath-agent`).

**Version.** By default, the one verified with this Yorath release; it is updated and rolled back
together with Yorath. A custom one: `yorath agent add openclaw --version 2026.11.5` or
`--version latest`; to change it, `yorath agent upgrade openclaw --version …`; without `--version`, back
to the verified one. A custom version is marked "not verified" and `yorath doctor` warns about it;
Yorath updates leave it alone. If the agent doesn't start on a custom version, the reason is in
`yorath agent list`, and the previous container keeps running.

**An agent you installed yourself.** Yorath finds OpenClaw, Hermes and other agents that run on your
servers around it: every node checks its host every few minutes and reports to the main node. They are
listed under Agents → "Found outside Yorath" with the node and how they run (a host process, as root or
not; a container; installed but not running). There is no switch: Yorath doesn't start them. "Connect"
creates an API key for the agent (shown once) and gives the commands for its settings. On a worker node the
model address goes through the cluster tunnel (`http://10.88.0.1:8000/v1`), so the agent needs neither
the Yorath certificate nor internet access; on the main node the commands also make the agent trust the
Yorath certificate. Once the key exists, the agent moves to "Connected by key" with the key's name and
its last model call. Its model calls show in the journal under the key's owner; revoking the key cuts it
off and puts the agent back among those to connect. Yorath still doesn't see or limit its tools and internet: for that,
connect the agent through Yorath instead (above).

**Telegram.** Each agent has its own bot: Yorath won't let two agents share one token. Change or
disable the bot on the agent's card or with `yorath agent telegram hermes` (`--off` disables it,
`--users 123,456` sets who it answers). The agent's memory is kept. Yorath stores the token encrypted,
but **the agent sees it itself** — it's the one talking to Telegram; this keeps things tidy, it's not
protection from the agent. A token from a file:
`YORATH_TELEGRAM_TOKEN_FILE=/root/bot.txt yorath agent telegram hermes`.

Server in Russia: intercepted agent traffic leaves the server directly, not through the VPN; if
`api.telegram.org` is blocked there, the bot won't work (a "node proxy → VPN" chain is the next step).

#### Is the bot working?

After you save a bot, the node the agent runs on asks Telegram about it (`getMe`) the way the agent
itself would: through the node proxy. The card shows the answer instead of just "bot set", and
"Check again" asks once more; otherwise the node asks every 10 minutes.

| Card says | What it means | What to do |
|---|---|---|
| Telegram accepts the bot @name | The node reaches Telegram and the token is valid | Write to the bot; it answers only the user ids you listed |
| Telegram is not reachable from <node> | The node proxy can't connect to `api.telegram.org`; the bot gets no messages | Usually the server's provider or country blocks Telegram. The bot works once the server reaches `api.telegram.org`; Yorath has no built-in way around a block yet |
| Telegram rejected the bot token | Telegram answered 401/404: the token is wrong or revoked | Get a token from @BotFather and save it again |
| The agent's internet is off | You turned the agent's internet off; Telegram needs it | "Turn on internet" on the card |
| Checking that Telegram reaches the bot… | The node hasn't checked yet; it does on its next pass, in seconds | Wait; if it stays, check that the node is active in the Nodes window |

The check can't tell whether the user ids are right: Telegram only shows that when that user writes
to the bot.
