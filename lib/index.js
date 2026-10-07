/**
 * dsh-memory — 全局记忆插件（host 半部）。
 *
 * 注册六个模型工具，内部通过 child_process 调用随包携带的 mem.py（纯标准库，
 * 零第三方依赖）。记忆库默认 $DSH_HOME/memories，可用插件配置 memoryHome 覆盖。
 */
import { defineTool } from "@deepseek-ai/dsh-tools";
import { execFile } from "node:child_process";
import { appendFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import os from "node:os";

export const name = "memory";
export const inject = ["tools", "sessions"];

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/**
 * Python 解释器候选，按优先级排列：插件配置 > 环境变量 > 平台惯例。
 * 逐个尝试，只有「解释器不存在」（ENOENT）才落到下一个候选；解释器存在但
 * mem.py 报错时会直接抛出，避免把真实错误伪装成「没装 Python」。
 */
function pythonCandidates(config) {
  const out = [];
  const add = (file, extra = []) => {
    if (typeof file === "string" && file.trim()) out.push({ file: file.trim(), extra });
  };
  add(config?.pythonPath);
  add(process.env.MEMORY_PYTHON);
  add(process.env.PYTHON);
  if (process.platform === "win32") {
    add("python");
    add("python3");
    add("py", ["-3"]);
  } else {
    add("python3");
    add("python");
  }
  return out;
}

// ---- 自动捕获（pending 缓冲）的隐私护栏 ----
// 系统注入前缀：命中即跳过，不落盘
const SKIP_PREFIXES = [
  "Current runtime context", "<system-reminder>", "[genui-action]",
  "The approval policy changed", "The available skill catalog changed",
  "You are repeating", "<memory", "</memory>"
];
// 单条捕获上限：防大段粘贴/代码/日志无差别落盘
const MAX_PENDING_CHARS = 500;
// 凭证/敏感模式：命中即跳过，避免把密码、API Key、验证码写进明文缓冲
const SENSITIVE_RE = [
  /(?:密码|口令|passwd|password|secret)\s*[:=：]\s*\S+/i,
  /(?:api[\s_-]?key|access[\s_-]?token|authorization|bearer)\s*[:=：]\s*\S+/i,
  /sk-[A-Za-z0-9_-]{16,}/i,
  /(?:验证码|校验码|一次性密码)\s*[:=：]?\s*\d{4,}/
];

function resolveRoot(config) {
  return config?.memoryHome || process.env.MEMORY_HOME
    || (process.env.DSH_HOME ? path.join(process.env.DSH_HOME, "memories") : path.join(os.homedir(), "memories"));
}

function pendingPath(root) {
  const d = new Date();
  const y = String(d.getFullYear());
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return path.join(root, "notes", "pending", y + "-" + m + "-" + day + ".md");
}

async function appendPending(root, text) {
  const p = pendingPath(root);
  await mkdir(path.dirname(p), { recursive: true });
  const hhmm = new Date().toTimeString().slice(0, 5);
  await appendFile(p, "- [" + hhmm + "] " + text + "\n", "utf8");
}

function userText(data) {
  const blocks = data && data.content;
  if (!Array.isArray(blocks)) return "";
  return blocks.filter(b => b && b.type === "text" && typeof b.text === "string")
    .map(b => b.text).join(" ").trim();
}

function apply(ctx, config) {
  const memPy = config?.memPyPath || path.join(__dirname, "..", "mem.py");
  const env = { ...process.env };
  if (config?.memoryHome) env.MEMORY_HOME = config.memoryHome;
  if (config?.semantic === true) env.MEMORY_SEMANTIC = "1";   // 语义检索默认关闭，见 README
  const root = resolveRoot(config);

  // 自动捕获：每轮用户消息原文追加到 notes/pending/日期.md（防漏记的兜底层）
  // 隐私护栏：过滤系统注入前缀 + 凭证类敏感内容 + 超长截断
  if (ctx.on) {
    ctx.on("session/event", (subject, event) => {
      try {
        if (!event || event.type !== "user/message") return;
        const text = userText(event.data);
        if (!text || SKIP_PREFIXES.some(p => text.startsWith(p))) return;
        if (SENSITIVE_RE.some(r => r.test(text))) return;
        const t = text.length > MAX_PENDING_CHARS
          ? text.slice(0, MAX_PENDING_CHARS) + "\n[已截断，超出 " + MAX_PENDING_CHARS + " 字符]"
          : text;
        appendPending(root, t).catch(() => {});
      } catch {}
    });
  }

  const pythons = pythonCandidates(config);
  // Windows 中文环境默认用 GBK 输出：不强制 UTF-8 的话，mem.py 的中文会
  // 在管道里变成乱码，或让 Node 按 UTF-8 解码时报错。两个变量一起设最稳。
  const utf8Env = { PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };
  const childEnv = { ...env, ...utf8Env };

  function execMem(python, args) {
    return new Promise((resolve, reject) => {
      execFile(python.file, [...python.extra, memPy, ...args],
        { env: childEnv, encoding: "utf8", timeout: 60000, windowsHide: true,
          stdio: ["ignore", "pipe", "pipe"] },   // 不给 stdin：避免子进程误读空管道而阻塞
        (err, stdout, stderr) => {
          if (err) {
            const e = new Error((stderr || err.message || "mem.py 执行失败").trim().slice(0, 800));
            e.code = err.code;
            reject(e);
          } else resolve(stdout.trim());
        });
    });
  }

  async function run(args) {
    let last;
    for (const python of pythons) {
      try {
        return await execMem(python, args);
      } catch (err) {
        last = err;
        if (err && err.code === "ENOENT") continue;   // 该候选不存在 → 试下一个
        throw err;
      }
    }
    throw new Error("找不到可用的 Python 解释器（已尝试: " + pythons.map((p) => p.file).join(", ")
      + "）。请安装 Python 3，或在插件配置里设置 pythonPath。" + (last ? " 最后错误: " + last.message : ""));
  }

  const out = {
    schema: { type: "object", additionalProperties: false, properties: {
      ok: { type: "boolean", required: true },
      output: { type: "string", required: true }
    } },
    render: (_a, v) => [{ type: "text", text: v.output }]
  };

  ctx.tools.register(defineTool({
    name: "memory_add",
    description: "把一次对话或一条信息写入全局记忆库。摘要必须结构化（结论/决定/待办/关键事实），标签逗号分隔；写入后自动进时间树和 FTS5 索引，检索时按相关性召回。",
    parameters: {
      title: { type: "string", required: true, description: "记忆标题" },
      summary: { type: "string", required: true, description: "结构化摘要：结论:..; 决定:..; 待办:..; 关键事实:.." },
      tags: { type: "string", description: "逗号分隔标签" },
      content: { type: "string", description: "正文细节（对话要点/原话）" },
      date: { type: "string", description: "YYYY-MM-DD HH:MM，默认现在" }
    },
    output: out,
    async execute(args) {
      const a = ["add", args.title];
      if (args.tags) a.push("-t", args.tags);
      if (args.summary) a.push("-s", args.summary);
      a.push("--content", args.content ?? "");   // 显式给出，绝不让 mem.py 去读 stdin
      if (args.date) a.push("--date", args.date);
      return { ok: true, output: await run(a) };
    },
    presentCall: (args) => ({ card: "generic", title: "写入记忆", kind: "other", rawInput: args })
  }));

  ctx.tools.register(defineTool({
    name: "memory_search",
    description: "检索全局记忆库：先搜摘要层（标题+摘要），miss 再搜正文层，返回 Top-K。查询前请先用它确认记忆是否存在，避免重复写入。",
    parameters: {
      query: { type: "string", required: true, description: "关键词（中文可用短语）" },
      k: { type: "integer", description: "返回条数，默认 5" }
    },
    output: out,
    async execute(args) {
      const a = ["search", args.query];
      if (args.k) a.push("-k", String(args.k));
      return { ok: true, output: await run(a) };
    },
    presentCall: (args) => ({ card: "generic", title: "检索记忆", kind: "other", rawInput: args })
  }));

  ctx.tools.register(defineTool({
    name: "memory_inject",
    description: "生成可直接注入上下文的记忆块：常驻层 MEMORY.md 条目 + 检索 Top-K 摘要。回答涉及过去讨论/偏好/约定时，用它把相关记忆拼进回答。",
    parameters: {
      query: { type: "string", required: true, description: "关键词" },
      k: { type: "integer", description: "Top-K，默认 3（token 纪律：≤3 条）" }
    },
    output: out,
    async execute(args) {
      const a = ["inject", args.query];
      a.push("-k", String(args.k ?? 3));   // token 纪律: 不传 k 默认 3
      return { ok: true, output: await run(a) };
    },
    presentCall: (args) => ({ card: "generic", title: "注入记忆", kind: "other", rawInput: args })
  }));

  ctx.tools.register(defineTool({
    name: "memory_remove",
    description: "删除某条记忆。默认软删除（进回收站可恢复）；hard=true 永久删除。删除后索引同步清理。",
    parameters: {
      path: { type: "string", required: true, description: "记忆文件路径，如 notes/2026/08/15-标题.md" },
      hard: { type: "boolean", description: "true=永久删除" }
    },
    output: out,
    async execute(args) {
      const a = ["rm", args.path];
      if (args.hard) a.push("--hard");
      return { ok: true, output: await run(a) };
    },
    presentCall: (args) => ({ card: "generic", title: "删除记忆", kind: "other", rawInput: args })
  }));

  ctx.tools.register(defineTool({
    name: "memory_list",
    description: "按时间列出记忆库内容（可选 YYYY-MM 过滤）。适合浏览/盘点已有哪些记忆。",
    parameters: {
      month: { type: "string", description: "YYYY-MM，默认全部" }
    },
    output: out,
    async execute(args) {
      const a = ["list"];
      if (args.month) a.push(args.month);
      return { ok: true, output: await run(a) };
    },
    presentCall: (args) => ({ card: "generic", title: "列出记忆", kind: "other", rawInput: args })
  }));

  ctx.tools.register(defineTool({
    name: "memory_mem",
    description: "管理常驻层 MEMORY.md（高频事实，≤2200 字符，会话开始时注入）。action: show 查看 / add 追加条目 / rm 按唯一子串删除条目 / clear 清空。",
    parameters: {
      action: { type: "string", required: true, enum: ["show", "add", "rm", "clear"], description: "操作" },
      text: { type: "string", description: "add=新条目文本；rm=唯一子串" }
    },
    output: out,
    async execute(args) {
      const a = ["mem", args.action];
      if (args.text) a.push(args.text);
      return { ok: true, output: await run(a) };
    },
    presentCall: (args) => ({ card: "generic", title: "常驻层管理", kind: "other", rawInput: args })
  }));

  return Promise.resolve();
}

export { apply };