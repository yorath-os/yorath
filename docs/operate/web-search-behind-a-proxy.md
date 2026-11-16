---
title: Web search behind a proxy
description: "Turn on web search, and route the core's outbound requests through a proxy when a service blocks your server's country."
---

The `research-agent` agent searches the web through the Tavily service.

1. Key: [app.tavily.com](https://app.tavily.com) → API Keys (the free tier is enough to try it).
2. Enter it on the wizard's "External services" step and agree that search query text is sent to
   Tavily.
3. Check: in Chat, pick `research-agent` and ask "What is the latest version of Python?" — the answer
   has `[1]`, `[2]` links, and the Journal shows a `web_search` call.

An answer without links and without a `web_search` call in the journal means the search didn't work;
see the [next section](#server-in-a-country-that-tavily-and-openrouter-block).

## Server in a country that Tavily and OpenRouter block

Tavily and OpenRouter block Russian IPs: they answer 403 even to a valid key. The agent then answers
"from memory", without links. To check:

```sh
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://api.tavily.com/search \
  -H "Authorization: Bearer $TAVILY_API_KEY" -H "Content-Type: application/json" -d '{"query":"t"}'
```

`403` is a country block; `401` means the service is reachable (any value of `TAVILY_API_KEY` will do,
even a made-up one).

The way out is to send the core's outbound HTTPS requests through your own HTTP proxy in another
country (for example, a VPN in a separate container on the same Docker network `yorath_default`), with
an install variable:

```sh
YORATH_EGRESS_PROXY=http://<proxy address>:<port> sh /opt/yorath/installer/install.sh
yorath doctor     # "Tavily is reachable via proxy", "OpenRouter is reachable via proxy"
```

The setting is saved in `installer/.env.local` and survives both a repeated `install.sh` and
`yorath update`. To remove it: `YORATH_EGRESS_PROXY=none sh /opt/yorath/installer/install.sh`. Only the
core's outbound requests go through the proxy; the model, database and web work directly.

`yorath doctor` checks Tavily and OpenRouter from the core's point of view and tells a country block
(403 for any key) from a wrong key.
