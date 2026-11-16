"""Tavily: web search for ``web_search``. The key lives only in the SecretStore, by consent."""

SECRET_NAME = "tavily_api_key"
LEAVES_SERVER = (
    "When searching the web, the query text written by the model leaves the server. "
    "With a Confidential or Restricted context, search is blocked by policy."
)
