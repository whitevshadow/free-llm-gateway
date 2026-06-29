"""
Custom exception classes for the Multi-LLM Gateway Platform.

WHY THIS MATTERS:
  In production backends, raw Python exceptions (ValueError, KeyError, etc.)
  should NEVER leak to the client. Instead, we define our own hierarchy of
  exceptions and catch them in a global handler that returns clean JSON.
  This is how teams at Stripe, GitHub, and every major API company do it.
"""

from typing import Optional


class GatewayException(Exception):
    """Base exception for the gateway. All custom exceptions inherit from this."""

    def __init__(
        self,
        message: str = "An unexpected error occurred",
        status_code: int = 500,
        error_code: str = "INTERNAL_ERROR",
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        super().__init__(self.message)


class ProviderNotFoundError(GatewayException):
    """Raised when the requested provider does not exist in our registry."""

    def __init__(self, provider: str):
        super().__init__(
            message=f"Provider '{provider}' is not supported. Check GET /api/v1/providers for available options.",
            status_code=400,
            error_code="PROVIDER_NOT_FOUND",
        )


class ProviderNotConfiguredError(GatewayException):
    """Raised when the provider exists but has no API key configured."""

    def __init__(self, provider: str):
        super().__init__(
            message=f"Provider '{provider}' is not configured. Set the corresponding API key in your .env file.",
            status_code=503,
            error_code="PROVIDER_NOT_CONFIGURED",
        )


class ModelNotFoundError(GatewayException):
    """Raised when the requested model is not in the provider's model list."""

    def __init__(self, model: str, provider: str):
        super().__init__(
            message=f"Model '{model}' is not available under provider '{provider}'.",
            status_code=400,
            error_code="MODEL_NOT_FOUND",
        )


class LLMTimeoutError(GatewayException):
    """Raised when an LLM request exceeds the configured timeout."""

    def __init__(self, provider: str, timeout: int):
        super().__init__(
            message=f"Request to '{provider}' timed out after {timeout}s.",
            status_code=504,
            error_code="LLM_TIMEOUT",
        )


class LLMCompletionError(GatewayException):
    """Raised when the LLM returns an error (rate limit, auth failure, etc.)."""

    def __init__(self, provider: str, detail: str):
        super().__init__(
            message=f"Provider '{provider}' returned an error: {detail}",
            status_code=502,
            error_code="LLM_COMPLETION_ERROR",
        )


class AllProvidersFailedError(GatewayException):
    """Raised when the primary call AND all fallbacks have failed."""

    def __init__(self, detail: str):
        super().__init__(
            message=f"All providers failed. Details: {detail}",
            status_code=502,
            error_code="ALL_PROVIDERS_FAILED",
        )
