from pydantic import BaseModel, ConfigDict, Field


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatSsoApproveRequest(StrictRequest):
    interaction_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ChatSsoExchangeRequest(ChatSsoApproveRequest):
    client_id: str = Field(min_length=1, max_length=64)
    approval_code: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ChatSsoStateRequest(StrictRequest):
    client_id: str = Field(min_length=1, max_length=64)
    binding_id: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ChatSsoApproveResponse(BaseModel):
    approval_code: str
    interaction_id: str
    finish_url: str


class ChatSsoStateResponse(BaseModel):
    active: bool
    sub: str | None
    binding_id: str
    auth_expires_at: int | None
    name: str | None = None
    username: str | None = None
    email: str | None = None
    email_verified: bool = False


class ChatSsoExchangeResponse(ChatSsoStateResponse):
    name: str
    username: str
