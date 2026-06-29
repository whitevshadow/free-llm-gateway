"""
Services package — business logic layer.

Route handlers call services; services call LiteLLM/database.
Route handlers should NEVER contain business logic directly.
"""

from app.services.llm_service import generate_completion, generate_completion_stream
from app.services.fallback import completion_with_fallback

__all__ = [
    "generate_completion",
    "generate_completion_stream",
    "completion_with_fallback",
]
