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
const PY = process.platform === "win32" ? "python" : "python3";

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
  const root = resolveRoot(config);

  // 自动捕获：每轮用户消息原文追加到 notes/pending/日期.md（防漏记的兜底层）
  if (ctx.on) {
    ctx.on("session/event", (subject, event) => {
      try {
        if (!event || event.type !== "user/message") return;
        const text = userText(event.data);
        if (!text || ["Current runtime context", "<system-reminder>", "[genui-action]", "The approval policy changed", "The available skill catalog changed"].some(p => text.startsWith(p))) return;
        appendPending(root, text).catch(() => {});
      } catch {}
    });
  }

  function run(args) {
    return new Promise((resolve, reject) => {
      execFile(PY, [memPy, ...args], { env, encoding: "utf8", timeout: 60000, windowsHide: true }, (err, stdout, stderr) => {
        if (err) reject(new Error((stderr || err.message || "mem.py 执行失败").trim().slice(0, 800)));
        else resolve(stdout.trim());
      });
    });
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
      if (args.content) a.push("--content", args.content);
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
      if (args.k) a.push("-k", String(args.k));
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