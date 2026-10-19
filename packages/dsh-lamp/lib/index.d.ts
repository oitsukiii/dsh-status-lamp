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
interface LampContext {
    on(name: string, listener: (...args: any[]) => void): () => void;
    effect?(fn: () => (() => void) | void): void;
    logger?: {
        info(msg: string): void;
        warn(msg: string): void;
    };
}
interface BridgeOptions {
    host?: string;
    port?: number;
}
export declare const name = "dsh-lamp-bridge";
export declare function apply(ctx: LampContext, options?: BridgeOptions): void;
export {};
