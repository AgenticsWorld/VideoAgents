// VideoAgents 删除守卫的 pi 扩展:core 启动 pi / Agentics(内置 pi)时以 `-e` 加载(`--no-extensions`
// 下显式 -e 仍会加载)。判定逻辑全在 delete_guard.py,这里只把每次工具调用转过去:
// - bash / powershell:字面写出的删除命中受保护目录(数据 / 项目目录本身或其上级)即拦下,说明作为
//   工具结果回给模型(`2>/dev/null || true` 也吞不掉);
// - read / write / edit / ls / find / grep:path 与磁盘真实大小写不一致时就地改成真实写法,回执随之
//   显示正确路径,免得模型以为多出一棵「重复目录」再去删(2026-10-22 事故的起因)。
// 守卫自身出错(找不到解释器、超时、输出不是 JSON)一律放行:pi 遇 tool_call 处理器抛错会拦下工具,
// 不能因守卫故障让 Agent 停工。环境变量由 core.delete_guard_env 注入,缺了就整个不生效。
import { execFile } from "node:child_process";

const PYTHON = process.env.VIDEOAGENTS_PYTHON;
const SCRIPT = process.env.VIDEOAGENTS_GUARD_SCRIPT;
const PATH_TOOLS = new Set(["read", "write", "edit", "ls", "find", "grep"]);

function judge(payload) {
  return new Promise((resolve) => {
    const child = execFile(PYTHON, ["-I", "-S", SCRIPT, "hook"],
      { timeout: 15000, maxBuffer: 1 << 20, windowsHide: true },
      (err, stdout) => {
        if (err) {
          process.stderr.write(`[delete-guard] ${err.message}\n`);
          return resolve({});
        }
        try {
          resolve(JSON.parse(stdout || "{}"));
        } catch {
          resolve({});
        }
      });
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify(payload));
  });
}

export default function (pi) {
  if (!PYTHON || !SCRIPT) return;
  pi.on("tool_call", async (event, ctx) => {
    const tool = event.toolName;
    const input = event.input || {};
    let sent;
    if ((tool === "bash" || tool === "powershell") && typeof input.command === "string") {
      sent = { command: input.command };
    } else if (PATH_TOOLS.has(tool) && typeof input.path === "string" && input.path) {
      sent = { path: input.path };   // 不带 write 的 content,免得大文件整份过管道
    } else {
      return undefined;
    }
    const verdict = await judge({ tool, input: sent, cwd: ctx?.cwd || process.cwd() });
    if (verdict.block) return { block: true, reason: String(verdict.reason || "blocked") };
    if (typeof verdict.path === "string" && verdict.path) input.path = verdict.path;
    return undefined;
  });
}
