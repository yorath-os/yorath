---
title: Codex CLI
description: "Run Codex CLI on your computer against the Yorath model, with every request in the Journal."
---

**You need a server with a GPU.** Codex's first request is about 12,700 tokens, and the cpu context window
is 4,096.

1. Install Codex (if you haven't): `brew install --cask codex` or `npm i -g @openai/codex`.
   The API keys screen shows ready-made snippets with your server's address after you create a key
   (the "Codex CLI" section).

2. Add a provider to `~/.codex/config.toml` in your home directory (leave the rest of the file alone; a project's `.codex/config.toml` doesn't read providers):

   ```toml
   [model_providers.yorath]
   name = "Yorath"
   base_url = "https://<IP>/v1"
   env_key = "YORATH_API_KEY"
   wire_api = "responses"
   ```

3. Put the profile in a **separate file** `~/.codex/yorath.config.toml` (since Codex 0.134, a
   `[profiles.yorath]` table in `config.toml` is a startup error):

   ```toml
   model_provider = "yorath"
   model = "local/default"
   model_context_window = 32768
   ```

4. Trust the certificate — see [Certificate](../operate/certificate.md).
5. Run:

   ```sh
   export YORATH_API_KEY=<key from the API keys screen>
   codex --profile yorath
   ```

Without `--profile yorath`, Codex works with OpenAI as before. Every request shows up in the Journal as
a `model-api` run on your behalf — with tokens and time, without text.

The warning `Model metadata for local/default not found` is harmless.

**Web search in Codex through Yorath isn't supported yet**: the Yorath server doesn't execute Codex's
built-in `web_search`, so the model answers from memory and may make things up. For "what is this"
questions, use `research-agent` in the Yorath chat.

## MCP servers in Codex

Configure them in the same `~/.codex/config.toml`, on your computer:

```toml
[mcp_servers.docs]
url = "https://<MCP address>"
bearer_token_env_var = "DOCS_MCP_TOKEN"     # the token goes in an environment variable, not in the file
```

Check: `codex mcp list`, and `/mcp` inside Codex. Calls to such an MCP server go from your computer and
bypass Yorath: they won't be in the Yorath journal, only the model calls will.
