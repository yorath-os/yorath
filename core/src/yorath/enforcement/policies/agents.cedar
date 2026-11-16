// Agent constraints are forbid: the intersection "user ∩ agent ∩ data".
// A Record is a member of its DataAsset, so `in` covers both documents and records.

@id("agent-not-allowed-for-asset")
forbid(
  principal,
  action in [Yorath::Action::"read_doc", Yorath::Action::"query_record"],
  resource
) unless { resource in context.agent.allowed_data };

@id("agent-class-ceiling")
forbid(
  principal,
  action in [Yorath::Action::"read_doc", Yorath::Action::"query_record"],
  resource
) when { resource.class > context.agent.max_class };

@id("agent-tool-not-allowed")
forbid(principal, action == Yorath::Action::"use_tool", resource)
unless { context.agent.allowed_tools.contains(resource.name) };

// A tool that sends data outside is not called with a context above its ceiling.
@id("tool-class-ceiling")
forbid(principal, action == Yorath::Action::"use_tool", resource)
when { context.context_class > resource.max_class };

// A service account acts only through its own agent (decision 27).
@id("service-account-own-agent")
forbid(principal is Yorath::ServiceAccount, action, resource)
when { principal.agent != context.agent };
