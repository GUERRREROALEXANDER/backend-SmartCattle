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
    # Whether JWT_SECRET is configured. The frontend needs this before showing
    # a sign-in form: with authentication off, /api/auth/* answers 503 and a
    # login attempt fails in a way that looks like a server fault rather than
    # a deployment that has not been configured.
    auth_enabled: bool
