"""Errors shared by the hosted and local inference clients."""


class InferenceUnavailable(RuntimeError):
    """An embedding or rerank service could not answer (network, quota, credentials, bad response)."""
