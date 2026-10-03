from pydantic import BaseModel


class WelcomeResponse(BaseModel):
    name: str
    version: str
    docs: str


class HealthResponse(BaseModel):
    status: str


class AIServiceStatus(BaseModel):
    configured: bool


class StatusResponse(BaseModel):
    status: str
    version: str
    ai_service: AIServiceStatus
    storage: str
