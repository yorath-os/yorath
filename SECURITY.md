# Security policy

## Report a vulnerability

Please don't open a public issue for a vulnerability. Report it privately, by either:

- email to **security@yorath.io**, or
- a private report on GitHub: the repository's **Security** tab → **Report a vulnerability**.

Include what is affected, the version (`yorath status`), and the steps to reproduce. If you can, attach the `yorath diag` archive — keys and passwords in it are redacted, but review it before sending.

## What happens next

1. We confirm we received your report within **7 days**.
2. We investigate and keep you informed.
3. We fix it in a release and credit you in the changelog, unless you prefer otherwise.

## Supported versions

Only the latest release gets security fixes. Update with `yorath update <version>`.

## Scope

How Yorath enforces access, and what it deliberately does not protect against (the server's root, the inside of connected agents, instructions injected into documents), is described at [yorath.io/security](https://yorath.io/security/).

## Known limits

These are known and deliberate in the current release; reports about them are still welcome if you see a way to make them worse than described.

- **No spending limit on `/v1`.** Any user can create a model key, and calls to external models (OpenRouter) spend the owner's balance with no cap per key or per user. Watch the cost in the Journal; budgets are planned.
- **Joining a node without TLS.** With `YORATH_TLS=off`, `install.sh --join http://…` sends the join token and the node token in clear text, and someone on the path could substitute the main node's tunnel key. Use it only inside a network you trust; the default (`YORATH_TLS=internal`) is encrypted.
- **Connected agents are shared.** OpenClaw and Hermes connected from the Yorath catalog belong to the installation, not to one user: any user can talk to them from the chat or with their own key, and the agent keeps one memory, including its Telegram conversations.
