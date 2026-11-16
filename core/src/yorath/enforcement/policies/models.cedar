// A model is a resource with allowed data classes (§6): context class not above the model ceiling.
// A violation is blocked; there is no silent switch to another model.

@id("model-allowed-class")
permit(principal, action == Yorath::Action::"call_model", resource)
when { context.context_class <= resource.max_class };
