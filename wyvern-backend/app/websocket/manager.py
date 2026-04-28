import json
from collections import defaultdict

from fastapi import WebSocket

from app.models.enums import PresenceStatus
from app.services.presence import presence_service


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        self.channel_subscribers: dict[str, set[str]] = defaultdict(set)
        self.voice_participants: dict[str, set[str]] = defaultdict(set)
        self.user_voice_channel: dict[str, str] = {}
        self.remote_voice_participants: dict[str, set[str]] = defaultdict(set)
        self.remote_user_voice_channel: dict[str, str] = {}
        self.remote_voice_profiles: dict[str, dict] = {}

    async def connect(self, user_id: str, websocket: WebSocket) -> None:
        if user_id in self.active_connections:
            previous = self.active_connections[user_id]
            await previous.close(code=4000)

        await websocket.accept()
        self.active_connections[user_id] = websocket
        await presence_service.set_presence(user_id, PresenceStatus.online)

    async def disconnect(self, user_id: str) -> tuple[str | None, set[str]]:
        self.active_connections.pop(user_id, None)

        for subscribers in self.channel_subscribers.values():
            subscribers.discard(user_id)

        left_channel_id, remaining = self.leave_voice(user_id)

        await presence_service.set_presence(user_id, PresenceStatus.offline)
        return left_channel_id, remaining

    def subscribe(self, user_id: str, channel_ids: list[str]) -> None:
        for channel_id in channel_ids:
            self.channel_subscribers[channel_id].add(user_id)

    def unsubscribe(self, user_id: str, channel_ids: list[str]) -> None:
        for channel_id in channel_ids:
            if channel_id in self.channel_subscribers:
                self.channel_subscribers[channel_id].discard(user_id)

    async def send_personal_message(self, user_id: str, payload: dict) -> None:
        websocket = self.active_connections.get(user_id)
        if websocket is None:
            return
        await websocket.send_text(json.dumps(payload))

    async def broadcast_to_users(self, user_ids: set[str], payload: dict) -> None:
        stale: list[str] = []
        for user_id in set(user_ids):
            websocket = self.active_connections.get(user_id)
            if websocket is None:
                continue
            try:
                await websocket.send_text(json.dumps(payload))
            except Exception:
                stale.append(user_id)

        for user_id in stale:
            self.active_connections.pop(user_id, None)

    async def broadcast_to_channel(
        self,
        channel_id: str,
        payload: dict,
        extra_user_ids: set[str] | None = None,
    ) -> None:
        recipients = set(self.channel_subscribers.get(channel_id, set()))
        if extra_user_ids:
            recipients.update(extra_user_ids)
        stale: list[str] = []

        for user_id in recipients:
            websocket = self.active_connections.get(user_id)
            if websocket is None:
                stale.append(user_id)
                continue

            try:
                await websocket.send_text(json.dumps(payload))
            except Exception:
                stale.append(user_id)

        for user_id in stale:
            recipients.discard(user_id)

    def join_voice(self, user_id: str, channel_id: str) -> tuple[str | None, set[str], set[str]]:
        previous_channel_id = self.user_voice_channel.get(user_id)
        previous_remaining: set[str] = set()

        if previous_channel_id is not None and previous_channel_id != channel_id:
            previous_set = self.voice_participants.get(previous_channel_id, set())
            previous_set.discard(user_id)
            if previous_set:
                previous_remaining = set(previous_set)
            else:
                self.voice_participants.pop(previous_channel_id, None)

        self.user_voice_channel[user_id] = channel_id
        self.voice_participants[channel_id].add(user_id)
        return previous_channel_id, set(self.voice_participants[channel_id]), previous_remaining

    def join_remote_voice(self, user_id: str, channel_id: str, user: dict | None = None) -> str | None:
        previous_channel_id = self.remote_user_voice_channel.get(user_id)

        if previous_channel_id is not None and previous_channel_id != channel_id:
            previous_set = self.remote_voice_participants.get(previous_channel_id, set())
            previous_set.discard(user_id)
            if not previous_set:
                self.remote_voice_participants.pop(previous_channel_id, None)

        self.remote_user_voice_channel[user_id] = channel_id
        self.remote_voice_participants[channel_id].add(user_id)
        if isinstance(user, dict):
            profile = {**user, "id": user_id}
            self.remote_voice_profiles[user_id] = profile
        return previous_channel_id

    def leave_voice(self, user_id: str, channel_id: str | None = None) -> tuple[str | None, set[str]]:
        active_channel_id = self.user_voice_channel.get(user_id)
        target_channel_id = channel_id if channel_id is not None else active_channel_id

        if target_channel_id is None:
            return None, set()
        if active_channel_id is not None and target_channel_id != active_channel_id:
            return None, set()

        participants = self.voice_participants.get(target_channel_id)
        if participants is None:
            if active_channel_id is not None:
                self.user_voice_channel.pop(user_id, None)
            return target_channel_id, set()

        participants.discard(user_id)
        self.user_voice_channel.pop(user_id, None)

        if not participants:
            self.voice_participants.pop(target_channel_id, None)
            return target_channel_id, set()
        return target_channel_id, set(participants)

    def leave_remote_voice(self, user_id: str, channel_id: str | None = None) -> str | None:
        active_channel_id = self.remote_user_voice_channel.get(user_id)
        target_channel_id = channel_id if channel_id is not None else active_channel_id

        if target_channel_id is None:
            return None
        if active_channel_id is not None and target_channel_id != active_channel_id:
            return None

        participants = self.remote_voice_participants.get(target_channel_id)
        if participants is not None:
            participants.discard(user_id)
            if not participants:
                self.remote_voice_participants.pop(target_channel_id, None)
        self.remote_user_voice_channel.pop(user_id, None)
        if user_id not in self.remote_user_voice_channel:
            self.remote_voice_profiles.pop(user_id, None)
        return target_channel_id

    def get_voice_participants(self, channel_id: str) -> set[str]:
        return set(self.voice_participants.get(channel_id, set())) | set(self.remote_voice_participants.get(channel_id, set()))

    def get_remote_voice_profiles(self, user_ids: set[str] | list[str] | tuple[str, ...] | None = None) -> dict[str, dict]:
        if user_ids is None:
            return dict(self.remote_voice_profiles)
        wanted = {str(user_id) for user_id in user_ids if str(user_id)}
        return {user_id: profile for user_id, profile in self.remote_voice_profiles.items() if user_id in wanted}

    def get_user_voice_channel(self, user_id: str) -> str | None:
        return self.user_voice_channel.get(user_id) or self.remote_user_voice_channel.get(user_id)

    def is_user_in_voice_channel(self, user_id: str, channel_id: str) -> bool:
        return self.is_local_user_in_voice_channel(user_id, channel_id) or self.is_remote_user_in_voice_channel(user_id, channel_id)

    def is_local_user_in_voice_channel(self, user_id: str, channel_id: str) -> bool:
        return self.user_voice_channel.get(user_id) == channel_id

    def is_remote_user_in_voice_channel(self, user_id: str, channel_id: str) -> bool:
        return self.remote_user_voice_channel.get(user_id) == channel_id

    def is_user_connected(self, user_id: str) -> bool:
        return user_id in self.active_connections


manager = ConnectionManager()
