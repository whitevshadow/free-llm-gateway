from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime

class TokenUsageLogResponse(BaseModel):
    id: int
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float
    latency: float
    status_code: int
    error_message: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class UsageSummaryItem(BaseModel):
    provider: str
    model: str
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost: float
    avg_latency: float

class AnalyticsDashboardResponse(BaseModel):
    total_cost: float
    total_requests: int
    total_tokens: int
    average_latency: float
    by_provider: List[UsageSummaryItem] = []
    recent_logs: List[TokenUsageLogResponse] = []
