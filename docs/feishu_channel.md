# FunHarness Feishu Channel

Run FunHarness as a Feishu/Lark bot gateway. By default it uses Feishu long
connection mode, so you do not need a public callback URL. The bot receives
Feishu messages, executes the local FunHarness agent, and sends
progress/tool/final messages back to the same chat.

## Feishu App Setup

Create a Feishu self-built app, enable bot capability, then subscribe to:

- `im.message.receive_v1`

In "Events & Callbacks", choose:

```text
Use long connection to receive events
```

Then add:

```text
im.message.receive_v1
```

Start the local FunHarness Feishu gateway, then click Verify/Save in Feishu.

Only use the HTTP callback URL if you explicitly set `FEISHU_EVENT_MODE=http`:

```text
http(s)://<your-public-host>/feishu/events
```

## Environment

Create or update `.env`:

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx

# Optional
FEISHU_VERIFICATION_TOKEN=xxx
FEISHU_HOST=0.0.0.0
FEISHU_PORT=8787
FEISHU_CALLBACK_PATH=/feishu/events
FEISHU_EVENT_MODE=ws
FEISHU_API_BASE=https://open.feishu.cn/open-apis
FEISHU_PERMISSION_MODE=suggest
FEISHU_WORKSPACE=defaultspace
```

Use `https://open.larksuite.com/open-apis` for Lark international tenants.

`FEISHU_PERMISSION_MODE=suggest` matches the TUI safety posture: read/web tools
run, while write/shell tools require approval and are denied remotely because
interactive approval is not implemented for Feishu yet. Use `auto` only in a
trusted workspace.

## Run

```bash
fh feishu
```

or:

```bash
fh-feishu
```

## Chat Commands

- `/help` - show Feishu channel help
- `/interrupt` - interrupt the current local agent run for this chat

Any other text is treated as a FunHarness prompt.
