"""Gateway denial: the reason from the PDP, without switching to another resource."""


class GatewayDenied(Exception):
    def __init__(self, code: str, reason: str, *, action: str, resource: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.action = action
        self.resource = resource


class GatewayError(Exception):
    """Gateway technical error (provider, tool, timeout), not a policy decision."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# HTTP statuses of the model queue refusals (change model-queue); other codes keep the status
# their endpoint already gives them
QUEUE_STATUS = {"queue_full": 429, "queue_timeout": 503}
