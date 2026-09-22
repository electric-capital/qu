## Twitter/X API (via authed_get)

Access the X API v2 using `authed_get` with the full API URL. Authentication is handled automatically -- the user's Twitter/X OAuth 2.0 token is injected as a Bearer header and refreshed transparently when expired.

**Base URL:** `https://api.twitter.com/2`

**Key API Paths:**

| Path | Description |
|------|-------------|
| `/2/users/me` | Get the authenticated user's profile (id, name, username) |
| `/2/users/{user_id}` | Look up a user by ID |
| `/2/dm_events` | List recent DM events across all conversations |
| `/2/dm_conversations/{dm_conversation_id}` | Get a DM conversation (use `expansions=participant_ids` for participants) |
| `/2/dm_conversations/{dm_conversation_id}/dm_events` | Get events in a specific conversation |
| `/2/users/{user_id}/bookmarks` | List a user's bookmarked tweets (only your own id works) |
| `/2/tweets/{tweet_id}` | Look up a single tweet by ID |

**Important: Get Your Own User ID First**

Before reading DM messages or bookmarks, call `/2/users/me` to get your own user ID. For DMs this lets you distinguish outgoing messages (where `sender_id` matches your ID) from incoming messages; for bookmarks the ID is required in the path.

```
tool_call(tool_name="authed_get", arguments={"url": "https://api.twitter.com/2/users/me?user.fields=id,name,username,profile_image_url"})
```

**Common Query Parameters** (upstream names, passed verbatim):

- `max_results`: Results per page (default 50, max 100)
- `pagination_token`: Cursor from a previous response's `meta.next_token`
- `dm_event.fields`: Fields on each DM event object, e.g. `id,text,created_at,sender_id,dm_conversation_id,attachments,referenced_tweets`
- `event_types`: DM event types to include (default `MessageCreate`)
- `tweet.fields`: Tweet fields, e.g. `id,text,created_at,author_id,conversation_id,referenced_tweets,public_metrics,entities,note_tweet`
- `expansions`: Comma-separated expansions -- inlines related objects under a top-level `includes` key, e.g. `sender_id,attachments.media_keys` (DMs) or `author_id,referenced_tweets.id,attachments.media_keys` (tweets)
- `user.fields`: Fields on expanded User objects in `includes.users`, e.g. `id,name,username,profile_image_url,description`
- `media.fields`: Fields on expanded Media objects in `includes.media`, e.g. `media_key,type,url,preview_image_url,alt_text`
- `poll.fields` / `place.fields`: Fields on expanded Poll/Place objects (tweets and bookmarks)

**Example tool calls:**

```
# List recent DM events with sender names inlined
tool_call(tool_name="authed_get", arguments={"url": "https://api.twitter.com/2/dm_events?dm_event.fields=id,text,created_at,sender_id,dm_conversation_id&expansions=sender_id&user.fields=id,name,username"})

# Get events in one conversation (next page)
tool_call(tool_name="authed_get", arguments={"url": "https://api.twitter.com/2/dm_conversations/123456789-987654321/dm_events?max_results=50&pagination_token=<token>"})

# List bookmarks (resolve your own id via /2/users/me first)
tool_call(tool_name="authed_get", arguments={"url": "https://api.twitter.com/2/users/<my_user_id>/bookmarks?tweet.fields=id,text,created_at,author_id,referenced_tweets,public_metrics&expansions=author_id&user.fields=id,name,username"})

# Look up a tweet with its thread parent inlined
tool_call(tool_name="authed_get", arguments={"url": "https://api.twitter.com/2/tweets/1234567890?tweet.fields=id,text,created_at,author_id,conversation_id,referenced_tweets&expansions=referenced_tweets.id&user.fields=id,name,username"})
```

**Thread Following:** When a tweet has a `referenced_tweets` field, it contains IDs of parent tweets (type `replied_to`) and quoted tweets (type `quoted`). Use `expansions=referenced_tweets.id` to inline referenced tweet content in `includes.tweets`. To walk up a thread, look up parent tweet IDs with `/2/tweets/{id}` and repeat. The `conversation_id` field is the same for all tweets in a thread (equal to the root tweet's ID), so when `id == conversation_id` you have reached the thread root.

**Using Field Arguments and Expansions:**

The `expansions` parameter causes the API to inline related objects under a top-level `includes` key in the response, eliminating the need for separate follow-up lookups. The most useful combination for reading DMs is `expansions=sender_id` with `user.fields=id,name,username` -- this adds sender display names directly in the response without a separate user lookup per message.

**Pagination:**

Twitter uses cursor-based pagination with `pagination_token` (a string, not an integer).
- First request: no `pagination_token`
- Subsequent pages: pass the `meta.next_token` from the previous response as `pagination_token`
- When `next_token` is null/absent, there are no more pages

**DM Conversation ID Format:**

One-to-one DM conversation IDs are formatted as `{smaller_user_id}-{larger_user_id}` (e.g., `123-456`). Group DM IDs are plain integers.

**Sending DMs (Requires Approval):**

To send a DM, create an action request via the `create_action_request` tool. Do NOT attempt a direct POST -- sending always requires user approval, and only the allow-listed GET paths above are reachable via `authed_get` anyway. `create_action_request` is top-level only -- if you are running as a sub-agent, do not call it; return the proposed `request_type` and `params` to the parent via `agent_task_response` instead.

Action request type: `send_twitter_dm`

Parameters:
- `message` (required): The text to send (max 10,000 characters)
- `dm_conversation_id` (optional): Send to an existing conversation
- `participant_id` (optional): Send to a user by their numeric ID (creates a new conversation if none exists)

You must provide either `dm_conversation_id` OR `participant_id`, not both.

**Important Notes:**
- Requires Twitter/X to be connected in Settings > Data Connections.
- Read-only via `authed_get`: only the GET paths on the allow-list in the Twitter plugin's `api.twitter.com` service entry are reachable; all writes go through `send_twitter_dm`.
- DM access requires the Basic API tier ($100/month minimum as of 2024)
- Rate limit: 15 requests per 15-minute window per user on DM endpoints
- Rate limit: 180 requests per 15-minute window per user on the bookmarks endpoint
- Rate limit: 900 requests per 15-minute window per user on tweet lookup
- Bookmarks access requires the `bookmark.read` scope -- users who connected Twitter before this scope was added must reconnect in Settings > Data Connections
- Twitter access tokens expire after 2 hours -- token refresh is handled automatically
- Messages are returned newest-first by default
