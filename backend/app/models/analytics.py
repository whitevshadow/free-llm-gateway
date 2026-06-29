from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from datetime import datetime, timezone
from app.core.database import Base

class TokenUsageLog(Base):
    __tablename__ = "token_usage_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    provider = Column(String, nullable=False)     # e.g., 'openai', 'gemini'
    model = Column(String, nullable=False)        # e.g., 'gpt-4o', 'gemini-1.5-pro'
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    cost = Column(Float, default=0.0)            # calculated cost in USD
    latency = Column(Float, default=0.0)         # call duration in seconds
    status_code = Column(Integer, default=200)   # HTTP status code or error state
    error_message = Column(String, nullable=True) # captured error message if failed
    
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
