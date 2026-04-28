# Wyvern FAQ

This FAQ is split into two audiences:
- `For Normal Users` covers everyday use of Wyvern.
- `For Developers` covers people using Wyvern's built-in developer-facing features, such as the API, webhooks, workspaces, and WebSocket integrations.

## For Normal Users

### What is Wyvern?
Wyvern is a web-based communication app for servers, channels, and direct messages. It is built for real-time chat, community organization, and lightweight collaboration.

### Is Wyvern like Discord?
Yes. The core model is very similar: you join servers, talk in channels, send direct messages, react to posts, and stay connected in real time.

### Do I need to install anything?
No. Wyvern is designed to run in a browser, though there may also be optional desktop or mobile apps depending on the client you use.

### What can I do in Wyvern?
You can create or join servers, chat in text channels, send DMs, edit and delete messages, react with emojis, upload files, manage your profile, and use voice-related presence features.

### Does Wyvern support real-time messaging?
Yes. Messages, reactions, DMs, member changes, and other events update live over WebSockets.

### Does Wyvern support voice chat?
Wyvern includes voice channel presence and WebRTC signaling for audio calls. That means it can support real-time voice-related coordination, but the experience depends on the client using those features.

### Does Wyvern have an official mobile app?
The backend is web-focused. Whether there is a separate mobile app depends on the front end you are using.

### How do I create an account?
You register with an email, username, and password. A display name is optional.

### Can I log in with just a username?
No. Login is email-based.

### Does Wyvern use passwords or tokens?
Both. You log in with a password, and the app uses access and refresh tokens after that.

### How long does a session last?
Access tokens are short-lived, and refresh tokens last longer. If the access token expires, the client can refresh it without making you log in again.

### What happens if I log out?
Your refresh token is revoked, which ends that session.

### Can I have more than one account?
The backend does not prevent multiple accounts, as long as each one uses a unique email.

### Can I change my username later?
Yes. Your profile can be updated after signup.

### What is the discriminator?
Each username gets a four-digit discriminator, so users can be uniquely identified even if they share the same name.

### Can I look up someone by name?
Yes. User lookup supports usernames and full `username#1234` style identifiers.

### What profile fields can I edit?
You can update your avatar, username, display name, bio, and directory visibility preference.

### What is the difference between username and display name?
Your username is your unique account name. Your display name is the friendlier label shown to other people.

### What is my bio for?
Your bio is a short public profile description.

### Can I hide myself from the user directory?
Yes. There is an opt-in user directory, and you can choose whether to appear there.

### Can other people see my presence?
Presence is tracked and can be retrieved by the app. Whether a specific UI shows it depends on the client.

### What presence states are available?
Presence is stored and updated as a status value. The exact options depend on the client implementation, but the backend supports updating and reading it.

### What is a server?
A server is a community space with its own channels and members.

### How do I create a server?
You create a server from the app, and you become its owner.

### Can I leave a server?
Yes, unless you are the server owner.

### Can the server owner leave?
No. The owner cannot leave the server.

### Can the owner delete a server?
Yes. Only the owner can delete the server.

### Who can edit a server?
Owners and admins can update server settings.

### What can be changed on a server?
Servers can have a name, description, icon, and directory visibility setting.

### What is the server directory?
It is an opt-in public directory of servers that have chosen to appear there.

### What does it mean to opt in to the directory?
It means your server can be listed publicly in the directory so people can discover it.

### Can I see whether I’ve already joined a listed server?
Yes. Directory listings can show whether your account is already a member.

### How do server invites work?
Members with the right permissions can generate invite codes. Other users can use the code to look up the server and join it.

### Are invite links permanent?
The backend generates invite codes and paths, but invite lifetime or expiration depends on the broader product rules, if any are added later.

### Can I join a server directly without an invite?
Yes, if the server is publicly joinable in the app flow or if you already have access to it through another route.

### Can I see the member list?
Yes, if you are already a member of the server.

### Can I see server activity?
Yes, members can view a server activity feed.

### What is a server role?
Members can have roles such as owner, admin, moderator, or member.

### Who can change member roles?
Only the server owner can change roles.

### Can the owner change their own role?
No. The owner role cannot be changed away from the owner account.

### What types of channels exist?
Wyvern supports text channels and voice channels.

### Can I create channels?
Yes, if you have permission in the server.

### Can I edit or delete channels?
Yes, if you have the right permissions.

### Do voice channels have the same features as text channels?
No. Voice channels are for voice-related presence and signaling. Text channels are used for messages.

### Can I DM other users?
Yes. You can create a direct message channel with another user.

### Can I DM myself?
No. Self-DMs are not allowed.

### What happens if a DM already exists?
Wyvern reuses the existing DM channel instead of creating a duplicate one.

### Can I hide a DM from my list?
Yes. Closing a DM hides it from your DM list without deleting the underlying channel for the other participant.

### If I reopen a DM, will it come back?
Yes. Starting the same DM again can restore it in your active DM list.

### Do both people see DM updates?
Yes. DM creation and deletion events are sent to the relevant participants in real time.

### Can I send messages in channels?
Yes. That is one of Wyvern’s core features.

### Can I send a message with only a file?
Yes. Messages can contain text, attachments, or both.

### Can I send an empty message?
No. A message must contain either text or attachments.

### Can I edit a message?
Yes, but only your own messages can be edited.

### Can I delete a message?
Yes. You can delete your own messages, and moderators can delete messages in server channels.

### Can moderators delete other people’s messages?
Yes, in non-DM channels, if they have moderation permissions.

### Can I reply to a specific message?
Yes. Replies must target a message in the same channel.

### Is message history paginated?
Yes. Message history uses cursor-based pagination.

### Can I search messages?
Yes. You can search messages by text and filter by channel, server, author, attachments, reactions, pinned status, and creation dates.

### Can I pin messages?
Yes. You can pin your own messages, and in server channels moderators can pin messages too.

### Can I bookmark messages?
Yes. Bookmarks are personal and saved per user.

### What is the difference between pinning and bookmarking?
Pinning is a channel-level action that highlights messages for the channel. Bookmarking is a private save for your own account.

### Can I react to messages?
Yes. You can add and remove emoji reactions.

### Can I use custom emoji?
The backend accepts emoji strings. Whether a client exposes custom emoji support depends on the front end.

### Do reactions update live?
Yes. Reaction adds and removals are pushed to connected clients in real time.

### Does Wyvern show if a message was edited?
Yes. Edited messages carry an edit timestamp.

### Can I upload files?
Yes. Wyvern supports multipart file uploads.

### Where are uploads stored?
Uploads are stored locally on the server and served back by the app.

### Is there a file size limit?
Yes. Free and paid users can have different upload limits.

### Are uploads rate-limited?
Yes. Uploads have rate limiting to prevent abuse.

### What types of files are allowed?
Uploads are checked for MIME type, and the storage flow has validation hooks to keep files safer.

### Are uploaded files public?
They are served from a media route, so anyone with access to the URL can request them.

### Can I attach multiple files?
The upload endpoint handles file uploads, and messages can carry attachment data. Exact multi-file behavior depends on the client.

### What does “real-time” mean in Wyvern?
It means updates appear instantly or near-instantly for connected users without needing to refresh the page.

### Do I need to keep the app open for messages to arrive?
Yes. Real-time updates are delivered over a WebSocket connection while the app is open.

### What kinds of events are sent live?
Messages, reactions, DMs, presence updates, server events, member changes, and voice-related events can all be delivered live.

### What if my connection drops?
The client should reconnect. The backend is built around live sessions, so reconnecting restores normal behavior.

### Is there a ping mechanism?
Yes. The WebSocket protocol includes a ping action.

### Does Wyvern have voice chat?
Wyvern supports voice channel presence and signaling for calls.

### Can I join or leave a voice channel?
Yes. Voice presence actions include joining, leaving, and checking voice status.

### Does Wyvern itself carry the audio stream?
The backend provides signaling. Audio transport is handled by WebRTC in the client.

### Can I place direct calls?
The protocol includes signaling for call offers, answers, and ICE candidates.

### Can I discover other users?
Yes. There is an opt-in user directory.

### Can I discover servers?
Yes. There is an opt-in server directory.

### Is directory participation optional?
Yes. Both user and server directory visibility are opt-in.

### Can I search for users by exact handle?
Yes. You can search by username or `username#1234`.

### Is there an admin page?
Yes. Wyvern includes an admin UI at `/admin`.

### Who can access admin features?
Admin access is controlled by an allowlist in `admins.json`.

### Can I make myself an admin from the UI?
No. Admin access is determined by the allowlist, not a self-service toggle.

### What can admins see?
The backend exposes an admin overview feed and related moderation or operational data.

### Can admins manage every server?
Not automatically. Admin access is separate from server ownership and server roles.

### Does Wyvern expose everything publicly?
No. Many features are scoped by server membership, DM participation, or opt-in directory settings.

### Can I view a server if I’m not a member?
No. Server details are restricted to members.

### Can I view a DM if I’m not in it?
No. DM channels are restricted to participants.

### Can I see all users in the system?
No. Only directory opt-in users are listed in the user directory.

### Are DMs hidden when I close them?
Yes. Closing a DM hides it for your account.

### Does Wyvern track message rate limits?
Yes. Messaging and uploads are rate-limited to protect the system.

### Why can’t I log in?
Usually this means the email or password is wrong, or the session token is invalid.

### Why can’t I join a server?
You may not have permission, the invite may be invalid, or you may not be logged in.

### Why can’t I see a server’s details?
You need to be a member of that server.

### Why can’t I see a DM?
You either are not a participant, or you previously closed it on your account.

### Why can’t I upload a file?
The file may be too large, blocked by rate limits, or rejected by MIME validation.

### Why do some pages or features feel missing?
Wyvern is actively developed, so some front-end surfaces may not expose every backend feature yet.

### What should I check first if something is broken?
Check whether you are logged in, whether the server or DM is accessible to you, and whether the feature depends on WebSocket connectivity, Redis, or PostgreSQL being available.

### Is Wyvern finished?
No. It is a working product, but it is still under active development.

### Is every feature fully polished?
Not necessarily. Some features are core and usable, while others are still rough or infrastructure-heavy.

### Can the product run without PostgreSQL?
No. PostgreSQL is required for the backend.

### Can the product run without Redis?
Not cleanly. Redis is used for presence, rate limiting, and real-time infrastructure.

### Does the backend store media locally?
Yes. Uploads are stored on the local filesystem unless the storage layer is changed later.

### Does Wyvern support multiple environments?
Yes. The config supports development and production-style deployment patterns.

### What is Wyvern best at?
Fast server-based chat with DMs, live updates, reactions, uploads, and community management.

### What is the simplest way to describe it?
It is a Discord-style web app built around real-time messaging and community spaces.

## For Developers

### What is a workspace?
A workspace is a document-like area attached to a channel for writing or code-style collaboration.

### Are workspaces public or private?
Both are possible. Workspaces can be public or private.

### What is a public workspace?
A public workspace is shared in the channel and can be visible to others in that channel.

### What is a private workspace?
A private workspace is owned by a specific user and visible only to that owner.

### Does editing a workspace save history?
Yes. Changes create revisions when the content changes.

### Can I choose the workspace language?
Yes. Supported languages include plaintext, Python, JavaScript, TypeScript, HTML, CSS, JSON, SQL, and Markdown.

### Can I build against an API?
Yes. Wyvern exposes a versioned API under `/api/v1`.

### Can I build my own client?
Yes, as long as it speaks to the backend’s auth, HTTP, and WebSocket flows.

### Does the API use consistent responses?
Yes. Successful responses include a `success`, `data`, and `error` structure.

### Can I get raw data from the backend?
Yes. The API is built around structured JSON responses.

### Can I listen for live events?
Yes. Wyvern uses WebSockets for real-time updates.

### What kinds of live events are available?
Messages, reactions, DMs, presence updates, server events, member changes, workspace updates, and voice-related events can be sent live.

### Is there a ping action for sockets?
Yes. The WebSocket protocol includes a ping action.

### Can I post as a webhook?
Yes. Webhooks can send messages into text channels.

### Do webhook messages look different?
They can. Webhook messages may show a webhook name and avatar instead of a normal user identity.

### Can webhooks be managed from the app?
Yes. You can create, list, and delete server webhooks if you have the right permissions.

### Can I get webhook delivery history?
Yes. The backend stores delivery logs for webhooks.

### Are webhook URLs secret?
Yes. Webhook URLs include a token and should be treated like credentials.

### Are webhooks limited to text channels?
Yes. Webhook channels must be text channels in the server.

### Can workspaces be used for code?
Yes. A workspace can be set to code or writing mode.

### Can a workspace be tied to a specific programming language?
Yes. The workspace model supports a language value, which can help a client tailor editing behavior.

### Can workspace visibility be changed?
Yes. A workspace can be switched between public and private visibility.

### Do public workspaces generate activity?
Yes, public workspace updates can be added to server activity if requested by the client flow.

### Is there a health check for integrations?
Yes. `/health` reports whether the backend is up.

### What should I know before integrating?
Uploads are rate-limited, message actions are rate-limited, DMs are participant-scoped, and many server routes require membership or role checks.

### Can I use the API without being an official Wyvern developer?
Yes. In this FAQ, "developer" means someone using Wyvern's developer-facing features, not someone building Wyvern itself.
