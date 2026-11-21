"""Errors shared by the hosted and local inference clients."""


class InferenceUnavailable(RuntimeError):
    """An embedding or rerank service could not answer (network, quota, credentials, bad response)."""


class QuotaExceeded(InferenceUnavailable):
    """The hosted service's free quota is used up (HTTP 429 after all retries)."""
