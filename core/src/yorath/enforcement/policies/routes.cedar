// Inter-agent route: only an explicitly allowed recipient, context class not above its ceiling.

@id("agent-to-agent")
permit(principal, action == Yorath::Action::"invoke_agent", resource)
when {
  context.agent.allowed_agents.contains(resource) &&
  context.context_class <= resource.max_class
};
