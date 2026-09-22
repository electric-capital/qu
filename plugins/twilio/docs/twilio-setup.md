# Twilio Setup

How to configure the Twilio SMS integration (served by the `plugins/twilio` plugin; see [twilio-api.md](twilio-api.md)).

## Twilio account

1. Create a Twilio account and, in the Console, note the **Account SID** (`AC...`) and **Auth Token** (Account Info on the dashboard).
2. Provision a sender: either buy an SMS-capable phone number, or create a **Messaging Service** (`MG...`) with at least one number in its sender pool. A Messaging Service is recommended for US A2P 10DLC compliance.
3. Trial accounts can only text numbers verified in the Twilio Console and prefix every message with a trial notice -- fine for development, not for real users.

## Quest admin configuration

Settings > Service Credentials > **Twilio SMS** (admin only, desktop):

| Field | Value |
|-------|-------|
| Account SID | `AC` + 32 hex characters |
| Auth Token | the account's Auth Token (stored masked; leave blank on later saves to keep it) |
| Sending number or Messaging Service SID | an E.164 number such as `+15551234567`, or an `MG...` SID |
| Trusted channel | see below |

The store file is `data/service_credentials/twilio.json`. The Data Connections row appears for users only once the first three fields are set.

### Trusted channel

Leave **off** (the default) unless texts to this deployment's users are known to reach only them. Off means Quest can text a user's own number only with messages that user pre-wrote in Settings > SMS Messages, word for word -- so a prompt-injected or confused model can never put arbitrary text on someone's phone unprompted. On means the `twilio_send_self_sms` tool may also send free-form text without an approval card. Texts to anyone else always require approval either way. The switch is read on every send, so changing it needs no restart.

## Per-user connection

Users open Settings > Data Connections > **+ Add Connection** > Twilio SMS, type their mobile number with country code, receive a 6-digit code by text, and type it back. The verified number is the only recipient `twilio_send_self_sms` can address. Users can then author their pre-written messages under Settings > **SMS Messages** (the section is listed only while the admin has configured Twilio).
