# DSH Status Lamp

A small desktop widget that tells you at a glance what DSH is doing: reasoning, running a command, waiting for your approval, or finished.

No window switching, no log tailing. A glance out of the corner of your eye.

> **How this was built**: driven end to end by **DSH** (DeepSeek Harness) running **DeepSeek V4.1 Flash**, as a vibecoding exercise. The sprites were generated with ComfyUI and then trimmed by the scripts under `tools/`.

[中文](README.md) | **English**

---

## 1. Preview

![The four states](docs/images/overview-en.png)

### What the lamp means

| Lamp | Meaning | When |
|---|---|---|
| 🔴 Red | **Needs you** | An approval dialog is open (waiting for allow)<br>The agent asked you a question |
| 🟡 Yellow | **Working** | Running a tool / reasoning / generating / retrying<br>A new turn just started |
| 🟢 Green | **Just finished** | Stays green for three minutes; turns yellow when you send the next message |
| ⚪ Off | **Idle** | Nothing in progress, or the data cannot be read |

---

## 2. Configuration

### Context menu

![Context menu](docs/images/menu-en.png)

| Item | What it does |
|---|---|
| **Refresh now** | Fetch the state again instead of waiting for the next push |
| **Reset position** | Bring the lamp back if it was dragged somewhere invisible |
| **Always on top** | Whether it floats above other windows |
| **Play a sound when a task completes** | A chime at the end of a turn |
| **Breathe the light** | The lit bulb cycles between 60% and 100% brightness, pausing a second at full |
| **Lamp size** | Small, medium or large; takes effect immediately |
| **Settings…** | Opens the window below |
| **Quit** | Closes the lamp |

**Each of these applies and saves immediately** -- there is no save button.

### Settings window

![Settings](docs/images/settings-en.png)

| Item | Meaning |
|---|---|
| **Language / 语言** | Chinese or English; the interface changes as soon as you pick one |
| **Plugin address** | The only required setting. `http://127.0.0.1:3099/events` when the lamp and DSH share a machine; the DSH host's LAN address otherwise |
| **Test connect** | One request to the bridge's `/state`, purely to check the network |
| **Installing the plugin and configuring it** | Two ways to install the bridge: copy a prompt for your agent, or copy the project address and do it by hand |

**Where the bridge goes** (and the address is that same machine):

| | How | |
|---|---|---|
| **DSH plugin** (recommended) | Add `github:oitsukiii/dsh-status-lamp#path:/packages/dsh-lamp` in DSH's Plugins page, then restart DSH | Starts with DSH, no process to watch, and subscribes to session events directly -- **the fastest** |
| **Python bridge** (fallback) | See [`docs/python-bridge.md`](docs/python-bridge.md) | No DSH restart needed, but it needs Python and a zstd decoder, and you have to keep it running |

Both expose the same endpoints, so the lamp's configuration does not change.

---

## 3. Getting started

### Download (Windows)

[**Download DSH-Lamp.exe**](https://github.com/oitsukiii/dsh-status-lamp/releases/latest) — no Python needed, just run it.

### From source

Python 3.9+, plus:

- **Pillow** — for the sprites (without it the lamp falls back to a drawn circle)
- **A zstd decoder** — only for the Python bridge; the plugin does not need one
  - Built in on Python 3.14+
  - Otherwise `pip install pyzstd`
  - There is a one-click install button in settings (visible in Python bridge mode only)

```bash
git clone https://github.com/oitsukiii/dsh-status-lamp.git
cd dsh-status-lamp
pip install pillow
python client/lamp.py
```

Right-click the lamp to open settings.

---

## 4. Technical notes

### How the state is derived

DSH writes two things:

| Data | Location | Character |
|---|---|---|
| State snapshot | `~/.dsh/storages/session_projcache/sessions/*.json` | Structured, but **written lazily** (every few tens of seconds) |
| Event log | `~/.dsh/sessions/<project>/<session>/session.v4.jsonl.zstd` | Written often, but compressed and hundreds of megabytes |

The **plugin** subscribes to `session/event`, so an event is visible the moment it is committed -- no polling, no decompression.
The **Python bridge** does it the hard way: polls the snapshot and decompresses the tail of the event log for detail, which ties it to DSH's write timing.

The event log is read by seeking to the end, finding a frame header and decompressing only the last few hundred kilobytes.

### On read receipts

**Not possible. DSH has no such concept.**

What was checked:

| Looked for | Found |
|---|---|
| Fields like `unread` / `markRead` / `lastRead` | Only "unread **bytes**" from truncated shell output -- nothing about people |
| The client reporting attention (`visibilitychange`, `document.hidden`, `hasFocus`) | Present, but only for local behaviour: releasing a microphone, cancelling a recording, retrying a request. Nothing is sent to the host |
| Client-to-host connection state | Network availability only, not "someone is looking" |
| The `delivery-accepted` event | A session-log format marker, **not** "the user received this" |

**The architectural reason**: the DSH web client never tells the host that a person is watching. The host knows an event was committed; it does not know whether anything rendered it on screen.

The nearest signals are all wrong:

```
the time of the last user/message   -- that is speaking, not reading
the time of the last event          -- that is DSH working, not the user
the client being online             -- a background tab counts as online
```

**So this lamp answers "what is DSH doing", not "have you read it".**

### Behaviour

- **Breathing** dims only the lit bulb, never the housing; turning it off returns to full brightness
- **Sound** plays a chime when a turn completes
- **Size** small / medium / large, keeping the window where it was
- **Bilingual** Chinese and English, switching immediately in both the menu and the windows
- **High DPI** declares per-monitor DPI awareness, so text stays sharp on scaled displays
- **Sprite handling** the scripts under `tools/` trim the side tabs, recolour the unlit bulbs and generate the brightness steps

---

## 5. License

MIT
