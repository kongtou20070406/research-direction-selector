/** Optional OMP footer. State belongs to each factory invocation, never the module. */
import { spawn } from "node:child_process";
import { isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const MAX_OUTPUT = 16 * 1024;
const MAX_INPUT = 64 * 1024;
const HELPER = fileURLToPath(new URL("../skills/research-direction-selector/scripts/rds_conversation_hook.py", import.meta.url));

function statusText(value) {
  const text = value?.status;
  return typeof text === "string" && text.startsWith("RDS | ") && text.length <= 160
    && !/[\x00-\x1f\x7f]/u.test(text) ? text : undefined;
}

/** Bounded, non-shell subprocess; failures are advisory and never escape to OMP. */
export function createHelperRunner({ helperPath = HELPER, python = process.env.RDS_PYTHON || "python",
  spawnProcess = spawn, timeoutMs = 3000 } = {}) {
  const timeout = Number.isFinite(timeoutMs) ? Math.max(1, Math.min(timeoutMs, 3000)) : 3000;
  return async (payload) => {
    let input;
    try { input = JSON.stringify(payload); } catch { return undefined; }
    if (Buffer.byteLength(input, "utf8") > MAX_INPUT) return undefined;
    return new Promise((resolve) => {
      let child, timer, finished = false, size = 0;
      const chunks = [];
      const finish = (value, kill = false) => {
        if (finished) return;
        finished = true;
        clearTimeout(timer);
        if (kill) { try { child?.kill(); } catch { /* Display only. */ } }
        resolve(value);
      };
      try {
        child = spawnProcess(python, ["-B", helperPath, "--host", "omp"], {
          shell: false, windowsHide: true, stdio: ["pipe", "pipe", "pipe"],
          env: { ...process.env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" },
        });
        timer = setTimeout(() => finish(undefined, true), timeout);
        child.on("error", () => finish(undefined, true));
        child.stdin.on("error", () => finish(undefined, true));
        child.stderr.resume();
        child.stdout.on("data", (chunk) => {
          const bytes = Buffer.from(chunk);
          size += bytes.length;
          if (size > MAX_OUTPUT) return finish(undefined, true);
          chunks.push(bytes);
        });
        child.on("close", (code) => {
          if (code !== 0) return finish();
          try {
            const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
            finish(statusText(value) || typeof value?.additionalContext === "string" && value.additionalContext.length <= MAX_OUTPUT ? value : undefined);
          } catch { finish(); }
        });
        child.stdin.end(input, "utf8");
      } catch { finish(undefined, true); }
    });
  };
}

function inputFor(event, ctx, allShell = false) {
  if (event?.toolName !== "bash" || typeof event.toolCallId !== "string"
    || !event.toolCallId || event.toolCallId.length > 256) return undefined;
  const args = event.args;
  if (!args || typeof args.command !== "string" || args.command.length > 16384
    || !allShell && !args.command.toLowerCase().includes("rds_cli.py")) return undefined;
  if (args.cwd !== undefined && args.workdir !== undefined && args.cwd !== args.workdir) return undefined;
  let cwd = args.cwd ?? args.workdir ?? ctx?.cwd;
  if (typeof cwd !== "string" || !cwd || cwd.length > 4096) return undefined;
  // OMP resolves bash's relative working directory against the session root.
  if (!isAbsolute(cwd)) {
    if (typeof ctx?.cwd !== "string" || !isAbsolute(ctx.cwd)) return undefined;
    cwd = resolve(ctx.cwd, cwd);
  }
  return { cwd, tool_name: "Bash", tool_use_id: event.toolCallId,
    tool_input: { command: args.command, cwd } };
}

function resultFor(event) {
  const response = {};
  // OMP carries tool metadata in result.details; content/stdout are never read.
  for (const source of [event.result, event.result?.details, event.details]) {
    if (!source || typeof source !== "object") continue;
    for (const key of ["exitCode", "exit_code"]) {
      if (Number.isInteger(source[key])) {
        // Conflicting structured codes remain a failure, not a false success.
        if (!(key in response) || response[key] === 0) response[key] = source[key];
      }
    }
    if (source.interrupted === true) response.interrupted = true;
    if (source.isError === true || source.is_error === true) response.isError = true;
    if (source.error === true || typeof source.error === "string" && source.error.length > 0) {
      response.error = "tool error"; // No error message or user log leaves the host.
    }
  }
  if (event.isError === true) response.isError = true;
  return response;
}

export function createRdsStatusExtension({ runHelper = createHelperRunner() } = {}) {
  return function rdsStatus(pi) {
    const calls = new Map();
    let epoch = 0, sequence = 0, completion = 0, lastFinished, lastContext;
    const seen = new Set();
    let queue = Promise.resolve();
    const schedule = (work) => {
      const result = queue.then(work, work);
      queue = result.catch(() => {});
      return result.catch(() => undefined);
    };
    const set = (ctx, text) => {
      if (!ctx || ctx.hasUI === false) return;
      try { ctx.ui?.setStatus("rds", text); } catch { /* A footer cannot fail a tool. */ }
    };
    const render = (ctx) => {
      lastContext = ctx;
      const active = [...calls.values()].filter((call) => call.status && !call.ended)
        .sort((a, b) => a.sequence - b.sequence);
      const text = active.at(-1)?.status;
      set(ctx, text ? text + (active.length > 1 ? ` (${active.length} active)` : "") : lastFinished?.text);
    };
    const reset = (_event, ctx) => {
      epoch++;
      calls.clear();
      seen.clear();
      lastFinished = undefined;
      if (lastContext !== ctx) set(lastContext, undefined);
      lastContext = ctx;
      set(ctx, undefined);
    };
    const invoke = async (payload) => {
      try { return statusText(await runHelper(payload)); } catch { return undefined; }
    };
    pi.on("tool_execution_start", async (event, ctx) => {
      const input = inputFor(event, ctx);
      if (!input || calls.size >= 128 || calls.has(event.toolCallId)) return;
      const call = { input, epoch, sequence: ++sequence, ended: false, status: undefined };
      calls.set(event.toolCallId, call);
      call.pre = invoke({ ...input, hook_event_name: "PreToolUse" });
      const status = await call.pre;
      if (call.epoch !== epoch || calls.get(event.toolCallId) !== call) return;
      call.status = status;
      if (!status && !call.ended) calls.delete(event.toolCallId);
      if (!call.ended) render(ctx);
    });
    pi.on("tool_execution_end", async (event, ctx) => {
      const call = calls.get(event?.toolCallId);
      if (!call || call.ended || event.toolName !== "bash") return;
      call.ended = true;
      call.completion = ++completion;
      render(ctx); // Never retain this call's running status after its end event.
      const recognized = await call.pre;
      if (call.epoch !== epoch || calls.get(event.toolCallId) !== call) return;
      const status = recognized ? await invoke({ ...call.input, hook_event_name: "PostToolUse",
        tool_response: resultFor(event), rds_status_only: true }) : undefined;
      if (call.epoch !== epoch || calls.get(event.toolCallId) !== call) return;
      calls.delete(event.toolCallId);
      if (recognized && (!lastFinished || call.completion > lastFinished.order)) {
        lastFinished = { order: call.completion, text: status };
      }
      render(ctx);
    });
    pi.on("tool_result", (event, ctx) => {
      const input = inputFor({ ...event, args: event.input }, ctx, true);
      if (!input || seen.has(event.toolCallId)) return;
      seen.add(event.toolCallId);
      const current = epoch;
      return schedule(async () => {
        if (current !== epoch) return;
        const response = resultFor(event);
        if (event.isError === false && response.exitCode === undefined && response.exit_code === undefined) response.exitCode = 0;
        const output = await runHelper({ ...input, hook_event_name: "PostToolUse", tool_response: response });
        if (current === epoch && typeof output?.additionalContext === "string" && output.additionalContext.trim())
          return { additionalContext: output.additionalContext };
      });
    });
    pi.on("before_agent_start", (_event, ctx) => {
      if (typeof ctx?.cwd !== "string" || !isAbsolute(ctx.cwd)) return;
      const current = epoch;
      return schedule(async () => {
        if (current !== epoch) return;
        const output = await runHelper({ cwd: ctx.cwd, hook_event_name: "UserPromptSubmit" });
        if (current === epoch && typeof output?.additionalContext === "string" && output.additionalContext.trim())
          return { message: { customType: "rds-reminder", content: output.additionalContext, display: false } };
      });
    });
    for (const event of ["session_start", "session_switch", "session_shutdown", "agent_end"]) pi.on(event, reset);
  };
}

export default createRdsStatusExtension();
