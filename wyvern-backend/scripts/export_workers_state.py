from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import (
    ApiToken,
    Channel,
    ChannelReadState,
    DMHiddenState,
    DMParticipant,
    Message,
    MessageBookmark,
    Reaction,
    RefreshToken,
    Server,
    ServerActivityLog,
    ServerInvite,
    ServerMember,
    ServerWebhook,
    UiVariantVote,
    User,
    WebhookDeliveryLog,
    WorkspaceDocument,
    WorkspaceRevision,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = Path(os.getenv('WYVERN_EXPORT_OUTPUT', PROJECT_ROOT / 'workers-state-export.json'))
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    raise SystemExit('DATABASE_URL is required')

SYNC_DB_URL = DATABASE_URL.replace('postgresql+asyncpg://', 'postgresql://', 1)
engine = create_engine(SYNC_DB_URL)

def iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)

state: dict[str, Any] = {
    'users': {},
    'refreshTokens': {},
    'apiTokens': {},
    'servers': {},
    'serverMembers': {},
    'serverInvites': {},
    'channels': {},
    'dmParticipants': {},
    'dmHiddenStates': {},
    'messages': {},
    'reactions': {},
    'messageBookmarks': {},
    'mediaObjects': {},
    'channelReadStates': {},
    'webhooks': {},
    'webhookDeliveries': {},
    'workspaceDocuments': {},
    'workspaceRevisions': {},
    'communityActivities': {},
    'realtimeEvents': {},
    'uiVariantVotes': {},
    'counters': {},
}

counters: dict[str, int] = {}

def track(prefix: str, value: str) -> None:
    try:
        num = int(value.split('_')[-1])
    except Exception:
        return
    counters[prefix] = max(counters.get(prefix, 0), num)

with Session(engine) as session:
    for row in session.scalars(select(User)).all():
        state['users'][row.id] = {
            'id': row.id,
            'username': row.username,
            'discriminator': row.discriminator,
            'display_name': row.display_name,
            'bio': row.bio,
            'directory_opt_in': row.directory_opt_in,
            'email': row.email,
            'avatar': row.avatar,
            'is_paid': row.is_paid,
            'created_at': iso(row.created_at),
            'password_hash': row.password_hash,
            'accepted_terms_version': row.accepted_terms_version,
            'accepted_privacy_version': row.accepted_privacy_version,
            'legal_accepted_at': iso(row.legal_accepted_at),
            'ai_opt_in': row.ai_opt_in,
            'nsfw_18_verified': row.nsfw_18_verified,
        }
        track('user', row.id)

    for row in session.scalars(select(RefreshToken)).all():
        state['refreshTokens'][row.id] = {
            'id': row.id,
            'user_id': row.user_id,
            'token_hash': row.token_hash,
            'expires_at': iso(row.expires_at),
            'created_at': iso(row.created_at),
            'is_revoked': row.is_revoked,
        }
        track('refresh_token', row.id)

    for row in session.scalars(select(ApiToken)).all():
        state['apiTokens'][row.id] = {
            'id': row.id,
            'user_id': row.user_id,
            'name': row.name,
            'token_hash': row.token_hash,
            'plaintext_token': row.token_ciphertext,
            'created_at': iso(row.created_at),
            'last_used_at': iso(row.last_used_at),
            'revoked_at': iso(row.revoked_at),
        }
        track('api_token', row.id)

    for row in session.scalars(select(Server)).all():
        state['servers'][row.id] = {
            'id': row.id,
            'name': row.name,
            'description': row.description,
            'icon': row.icon,
            'directory_opt_in': row.directory_opt_in,
            'owner_id': row.owner_id,
            'created_at': iso(row.created_at),
        }
        track('server', row.id)

    for row in session.scalars(select(ServerMember)).all():
        key = f'{row.server_id}:{row.user_id}'
        state['serverMembers'][key] = {
            'server_id': row.server_id,
            'user_id': row.user_id,
            'role': str(row.role.value if hasattr(row.role, 'value') else row.role),
            'joined_at': iso(row.joined_at),
        }

    for row in session.scalars(select(ServerInvite)).all():
        state['serverInvites'][row.code] = {
            'code': row.code,
            'server_id': row.server_id,
            'created_by': row.created_by,
            'created_at': iso(row.created_at),
        }
        track('server_invite', row.id)

    for row in session.scalars(select(Channel)).all():
        state['channels'][row.id] = {
            'id': row.id,
            'server_id': row.server_id,
            'name': row.name,
            'type': str(row.type.value if hasattr(row.type, 'value') else row.type),
            'position': row.position,
            'category': row.category,
            'created_by': row.created_by,
            'created_at': iso(row.created_at),
        }
        track('channel', row.id)

    for row in session.scalars(select(DMParticipant)).all():
        key = f'{row.channel_id}:{row.user_id}'
        state['dmParticipants'][key] = {
            'channel_id': row.channel_id,
            'user_id': row.user_id,
            'joined_at': iso(row.created_at),
        }

    for row in session.scalars(select(DMHiddenState)).all():
        key = f'{row.channel_id}:{row.user_id}'
        state['dmHiddenStates'][key] = {
            'channel_id': row.channel_id,
            'user_id': row.user_id,
            'hidden_at': iso(row.hidden_at),
        }

    for row in session.scalars(select(Message)).all():
        state['messages'][row.id] = {
            'id': row.id,
            'channel_id': row.channel_id,
            'author_id': row.author_id,
            'reply_to_id': row.reply_to_id,
            'content': row.content,
            'attachments': row.attachments or [],
            'created_at': iso(row.created_at),
            'edited_at': iso(row.edited_at),
            'is_pinned': row.is_pinned,
            'is_nsfw': row.is_nsfw,
            'webhook_name': row.webhook_name,
            'webhook_avatar': row.webhook_avatar,
        }
        track('message', row.id)

    for row in session.scalars(select(Reaction)).all():
        key = f'{row.message_id}:{row.emoji}:{row.user_id}'
        state['reactions'][key] = {
            'message_id': row.message_id,
            'emoji': row.emoji,
            'user_id': row.user_id,
        }

    for row in session.scalars(select(MessageBookmark)).all():
        state['messageBookmarks'][row.id] = {
            'id': row.id,
            'user_id': row.user_id,
            'message_id': row.message_id,
            'created_at': iso(row.created_at),
        }
        track('message_bookmark', row.id)

    for row in session.scalars(select(ChannelReadState)).all():
        key = f'{row.channel_id}:{row.user_id}'
        state['channelReadStates'][key] = {
            'channel_id': row.channel_id,
            'user_id': row.user_id,
            'last_read_message_id': row.last_read_message_id,
            'last_read_at': iso(row.last_read_at),
            'updated_at': iso(row.updated_at),
        }
        track('channel_read_state', row.id)

    for row in session.scalars(select(ServerWebhook)).all():
        state['webhooks'][row.id] = {
            'id': row.id,
            'server_id': row.server_id,
            'channel_id': row.channel_id,
            'name': row.name,
            'description': row.description,
            'active': row.active,
            'created_by': row.created_by,
            'created_at': iso(row.created_at),
            'updated_at': iso(row.updated_at),
            'last_used_at': iso(row.last_used_at),
            'token_plaintext': row.token_ciphertext,
            'token_hash': row.token_hash,
        }
        track('server_webhook', row.id)

    for row in session.scalars(select(WebhookDeliveryLog)).all():
        state['webhookDeliveries'][row.id] = {
            'id': row.id,
            'webhook_id': row.webhook_id,
            'request_id': row.request_id,
            'status': row.status,
            'attempts': row.attempts,
            'response_message': row.response_message,
            'created_at': iso(row.created_at),
        }
        track('webhook_delivery', row.id)

    for row in session.scalars(select(WorkspaceDocument)).all():
        state['workspaceDocuments'][row.id] = {
            'id': row.id,
            'channel_id': row.channel_id,
            'title': row.title,
            'mode': row.mode,
            'language': row.language,
            'visibility': row.visibility,
            'content': row.content,
            'owner_user_id': row.owner_user_id,
            'updated_by_user_id': row.updated_by_user_id,
            'created_at': iso(row.created_at),
            'updated_at': iso(row.updated_at),
        }
        track('workspace_document', row.id)

    for row in session.scalars(select(WorkspaceRevision)).all():
        state['workspaceRevisions'][row.id] = {
            'id': row.id,
            'document_id': row.document_id,
            'editor_user_id': row.editor_user_id,
            'content': row.content,
            'created_at': iso(row.created_at),
        }
        track('workspace_revision', row.id)

    for row in session.scalars(select(ServerActivityLog)).all():
        state['communityActivities'][row.id] = {
            'id': row.id,
            'server_id': row.server_id,
            'actor_user_id': row.actor_user_id,
            'action': row.action,
            'target_type': row.target_type,
            'target_id': row.target_id,
            'activity_metadata': row.activity_metadata,
            'created_at': iso(row.created_at),
        }
        track('server_activity_log', row.id)

    for row in session.scalars(select(UiVariantVote)).all():
        state['uiVariantVotes'][row.user_id] = {
            'user_id': row.user_id,
            'variant_key': row.variant_key,
            'updated_at': iso(row.updated_at),
        }
        track('ui_variant_vote', row.id)

state['counters'] = counters
OUTPUT_PATH.write_text(json.dumps(state, indent=2), encoding='utf-8')
print(str(OUTPUT_PATH))
