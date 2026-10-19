# dsh-lamp-bridge

DSH plugin that serves session state over HTTP + SSE for the [DSH Status Lamp](../../README.md).

Unlike the Python bridge in `bridge/`, this subscribes to `session/event` inside the
DSH process, so there is no polling and no zstd decoding — the state is current the
moment an event is committed.

## Install

In DSH's **Plugins** page, add:

```
github:oitsukiii/dsh-status-lamp#path:/packages/dsh-lamp
```

Then point the lamp at `http://<this machine>:3099/events`.

## Endpoints

| Path | What |
|---|---|
| `GET /state` | Current state as JSON, one shot |
| `GET /events` | SSE stream, pushes on every state change |

The response shape matches the Python bridge, so the desktop client needs no changes.

## Config

| Option | Default | |
|---|---|---|
| `host` | `0.0.0.0` | Interface to listen on |
| `port` | `3099` | Same as the Python bridge |

Set them in the plugin's config block in `cordis.patch.yml`:

```yaml
- id: dsh-lamp-bridge
  name: 'dsh-lamp-bridge'
  config:
    port: 3099
```
