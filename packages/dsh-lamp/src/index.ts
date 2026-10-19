/**
 * DSH Status Lamp — 桥（插件版）。
 *
 * 监听 DSH 的会话事件，把状态折叠成四种灯色，通过 HTTP + SSE 推给桌面小挂件。
 *
 * 和 Python 版（`bridge/state_bridge.py`）的区别只在数据来源：
 *   Python 版：轮询 projcache + 解压事件日志尾部 —— 受落盘时机影响
 *   本插件  ：直接订阅 `session/event` —— 事件提交的瞬间就拿到，无轮询
 *
 * 对外接口和 Python 版完全一致，客户端不需要改：
 *   GET /state   → 当前状态 JSON
 *   GET /events  → SSE 流，状态一变就推
 */

import { createServer, type Server, type ServerResponse } from 'node:http'

// Cordis 的 Context 类型：只用到 on / effect，避免依赖完整类型包。
interface LampContext {
  on(name: string, listener: (...args: any[]) => void): () => void
  effect?(fn: () => (() => void) | void): void
  logger?: { info(msg: string): void; warn(msg: string): void }
}

/** 灯语四色。 */
type LampState = 'question' | 'retrying' | 'tool' | 'working' | 'done' | 'idle'

interface LampSnapshot {
  state: LampState
  /** 需要你介入时的细分原因：审批 or 提问。 */
  attention_reason: 'approval' | 'question' | null
  /** 正在跑的工具名。 */
  tool: string | null
  /** 最后一条事件的名字，调试用。 */
  last_event: string | null
  /** 当前会话 id 的前 12 位。 */
  session: string | null
  /** 距最后一条事件多少秒。 */
  age_s: number | null
  /** done 状态已经持续多少秒（非 done 时为 null）。 */
  done_age_s: number | null
  /** 走插件版时固定为 "plugin"，方便对照。 */
  source: 'plugin'
  /** 这个桥自己的地址，客户端可以直接填。 */
  endpoint: string
  /** 更新时间，ISO 字符串。 */
  updated_at: string
}

/** 这些工具一被调用就是在等人，不是普通的干活。 */
const ATTENTION_TOOLS = new Set(['ask_user_question'])

/** done 保持多久后回落到 idle。 */
const DONE_WINDOW_MS = 180_000

/** 心跳间隔，防止中间网络设备掐掉空闲连接。 */
const HEARTBEAT_MS = 20_000

/** 默认监听端口，和 Python 版保持一致，客户端不用改配置。 */
const DEFAULT_PORT = 3099
const DEFAULT_HOST = '0.0.0.0'

interface BridgeOptions {
  host?: string
  port?: number
}

export const name = 'dsh-lamp-bridge'

export function apply(ctx: LampContext, options: BridgeOptions = {}): void {
  const host = options.host ?? DEFAULT_HOST
  const port = options.port ?? DEFAULT_PORT

  // ── 状态 ────────────────────────────────────────────────────────────────
  let state: LampState = 'idle'
  let attentionReason: LampSnapshot['attention_reason'] = null
  let toolName: string | null = null
  let lastEvent: string | null = null
  let sessionId: string | null = null
  let lastEventAt = 0
  let doneAt = 0

  const clients = new Set<ServerResponse>()

  function snapshot(): LampSnapshot {
    const now = Date.now()
    return {
      state,
      attention_reason: attentionReason,
      tool: toolName,
      last_event: lastEvent,
      session: sessionId ? sessionId.slice(0, 12) : null,
      age_s: lastEventAt ? Math.round((now - lastEventAt) / 100) / 10 : null,
      done_age_s: state === 'done' && doneAt ? Math.round((now - doneAt) / 100) / 10 : null,
      source: 'plugin',
      endpoint: `http://${host === '0.0.0.0' ? '127.0.0.1' : host}:${port}/events`,
      updated_at: new Date(now).toISOString(),
    }
  }

  /** 状态指纹，用来判断"值没变就别推"。 */
  function fingerprint(s: LampSnapshot): string {
    return `${s.state}|${s.attention_reason}|${s.tool}|${s.last_event}`
  }

  let lastFingerprint = ''

  function broadcast(force = false): void {
    const snap = snapshot()
    const fp = fingerprint(snap)
    if (!force && fp === lastFingerprint) return
    lastFingerprint = fp

    const payload = `data: ${JSON.stringify(snap)}\n\n`
    for (const res of clients) {
      try {
        res.write(payload)
      } catch {
        clients.delete(res)
      }
    }
  }

  // ── 事件 → 状态 ─────────────────────────────────────────────────────────
  function onSessionEvent(session: any, event: any): void {
    const type = String(event?.type ?? '')
    const data = event?.data ?? {}
    const sid = String(session?.id ?? session?.sessionId ?? '')

    // 多会话并存时，只看最近有动静的那个 —— 和 Python 版一致。
    if (sid && sessionId && sid !== sessionId) {
      // 换会话了：重置，避免把上一个会话的 done 带到新会话上
      state = 'idle'
      attentionReason = null
      toolName = null
      doneAt = 0
    }
    if (sid) sessionId = sid

    lastEvent = type
    lastEventAt = Date.now()

    switch (type) {
      case 'turn/start':
        state = 'working'
        attentionReason = null
        toolName = null
        doneAt = 0
        break

      case 'step/start':
        // 推理/生成中。工具还没开始。
        if (state !== 'question') {
          state = 'working'
          attentionReason = null
          toolName = null
        }
        break

      case 'tool/call': {
        const called = String(data?.name ?? '')
        toolName = called || null
        if (ATTENTION_TOOLS.has(called)) {
          state = 'question'
          attentionReason = 'question'
        } else {
          state = 'tool'
          attentionReason = null
        }
        break
      }

      case 'tool/result':
        // 工具回来了，回到"干活中"。下一个 step/start 会再确认一次。
        toolName = null
        if (state !== 'question') {
          state = 'working'
          attentionReason = null
        }
        break

      case 'approval/asked':
        state = 'question'
        attentionReason = 'approval'
        break

      case 'approval/decided':
        // 点完了，继续干。
        state = 'working'
        attentionReason = null
        break

      case 'llm/retry':
      case 'llm/retry-started':
        state = 'retrying'
        attentionReason = null
        break

      case 'assistant/message':
        if (state !== 'question') {
          state = 'working'
          attentionReason = null
        }
        break

      case 'turn/end':
        state = 'done'
        attentionReason = null
        toolName = null
        doneAt = Date.now()
        break

      default:
        // 其他事件只更新 lastEvent / lastEventAt，不动状态。
        return
    }

    broadcast()
  }

  // ── 定时器：done 回落 + 心跳 ───────────────────────────────────────────
  const ticker = setInterval(() => {
    if (state === 'done' && doneAt && Date.now() - doneAt > DONE_WINDOW_MS) {
      state = 'idle'
      doneAt = 0
      broadcast()
      return
    }
    // 心跳：即便状态没变也发一行注释，让客户端知道连接还活着。
    for (const res of clients) {
      try {
        res.write(': ping\n\n')
      } catch {
        clients.delete(res)
      }
    }
    // done 的 age 在变化，让它保持推送（客户端据此显示"刚完成 12 秒"）
    if (state === 'done') broadcast()
  }, Math.min(HEARTBEAT_MS, 5_000))
  ticker.unref?.()

  // ── HTTP + SSE ─────────────────────────────────────────────────────────
  const server: Server = createServer((req, res) => {
    const path = (req.url ?? '/').split('?')[0]

    if (path === '/events') {
      res.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        Connection: 'keep-alive',
      })
      // 连上先给一条当前值，客户端不用等下一次变化
      res.write(`data: ${JSON.stringify(snapshot())}\n\n`)
      clients.add(res)
      req.on('close', () => clients.delete(res))
      return
    }

    if (path === '/state' || path === '/') {
      const body = JSON.stringify(snapshot())
      res.writeHead(200, {
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Length': Buffer.byteLength(body),
        'Cache-Control': 'no-store',
      })
      res.end(body)
      return
    }

    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('only /state and /events are served\n')
  })

  server.on('error', (err: Error) => {
    ctx.logger?.warn(`dsh-lamp-bridge: server error: ${err.message}`)
  })

  ctx.effect?.(() => {
    server.listen(port, host, () => {
      ctx.logger?.info(`dsh-lamp-bridge listening on http://${host}:${port}`)
    })
    return () => {
      clearInterval(ticker)
      for (const res of clients) {
        try {
          res.end()
        } catch {
          /* ignore */
        }
      }
      clients.clear()
      server.close()
    }
  })

  ctx.on('session/event', onSessionEvent)
}
