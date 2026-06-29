from pydantic import BaseModel, ConfigDict
from typing import Optional, List
from datetime import datetime

class ChatMessageBase(BaseModel):
    role: str
    content: str

class ChatMessageCreate(ChatMessageBase):
    pass

class ChatMessageResponse(ChatMessageBase):
    id: int
    session_id: str
    provider: Optional[str] = None
    model: Optional[str] = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost: float
    latency: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ChatSessionBase(BaseModel):
    title: str

class ChatSessionCreate(BaseModel):
    title: Optional[str] = "New Chat"

class ChatSessionResponse(ChatSessionBase):
    id: str
    user_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ChatSessionDetailResponse(ChatSessionResponse):
    messages: List[ChatMessageResponse] = []

    model_config = ConfigDict(from_attributes=True)

class ChatPromptRequest(BaseModel):
    message: str
    provider: str      # e.g., 'openai', 'gemini'
    model: str         # e.g., 'gpt-4o', 'gemini-1.5-pro'
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 2048
