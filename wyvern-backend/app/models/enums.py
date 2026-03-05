import enum


class PresenceStatus(str, enum.Enum):
    online = "online"
    idle = "idle"
    dnd = "dnd"
    offline = "offline"


class MemberRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    moderator = "moderator"
    member = "member"


class ChannelType(str, enum.Enum):
    text = "text"
    voice = "voice"
    dm = "dm"
