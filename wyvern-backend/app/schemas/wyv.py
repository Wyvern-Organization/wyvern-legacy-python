from datetime import datetime

from pydantic import BaseModel, EmailStr


class WyvHandoffOut(BaseModel):
    grant: str
    expires_at: datetime


class WyvBridgeGrantRequest(BaseModel):
    grant: str


class WyvApiTokenIntrospectRequest(BaseModel):
    token: str


class WyvBridgeUserOut(BaseModel):
    user_id: str
    sync_id: str | None = None
    username: str
    discriminator: str
    display_name: str | None = None
    email: EmailStr
    avatar: str | None = None
    bio: str | None = None
    directory_opt_in: bool = False
    ai_opt_in: bool = False
    nsfw_18_verified: bool = False


class WyvApiTokenIntrospectionOut(BaseModel):
    active: bool = True
    token_id: str
    token_name: str
    user: WyvBridgeUserOut
