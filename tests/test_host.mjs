#!/usr/bin/env node
/**
 * 宿主半部冒烟测试：用 DSH 运行时**真实**的 `@deepseek-ai/dsh-tools` 加载 lib/index.js，
 * 再真的调用一次工具（Node → Python → mem.py），用来回答一个问题：
 * 「这个插件在当前这台机器的 DSH 版本上到底还能不能跑？」
 *
 * 运行（必须在 DSH 桌面端的 Electron 里跑，因为要读 app.asar 里的模块）：
 *
 *   cmd /c "set ELECTRON_RUN_AS_NODE=1&& \"D:\dsh\DeepSeek Harness.exe\" tests\test_host.mjs"
 *
 * 找不到 DSH 运行时（例如在普通 node 下跑整套测试）时打印 SKIP 并以 0 退出，
 * 不阻塞 `python tests/test_mem.py` 这条主测试线。
 */
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { pathToFileURL } from "node:url";
import { register } from "node:module";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";

const TOOLS_REL = join("resources", "app.asar", "dsh", "node_modules", "@deepseek-ai", "dsh-tools");
const EXPECTED = ["memory_add", "memory_search", "memory_inject", "memory_remove", "memory_list", "memory_mem"];

function findToolsDir() {
  const candidates = [];
  if (process.env.DSH_TOOLS_DIR) candidates.push(process.env.DSH_TOOLS_DIR);
  candidates.push(join(dirname(process.execPath), TOOLS_REL));
  return candidates.find((dir) => existsSync(join(dir, "lib", "index.js")));
}

const toolsDir = findToolsDir();
if (!toolsDir) {
  console.log("SKIP: 找不到 DSH 运行时里的 @deepseek-ai/dsh-tools。");
  console.log("      请在 DSH 桌面端目录下用 ELECTRON_RUN_AS_NODE=1 运行本脚本，或用 DSH_TOOLS_DIR 指定。");
  process.exit(0);
}
process.env.DSH_TOOLS_ENTRY = pathToFileURL(join(toolsDir, "lib", "index.js")).href;
register("./harness-tools-resolve.mjs", import.meta.url);

const plugin = await import("../lib/index.js");

function fail(message) {
  console.error("FAIL: " + message);
  process.exitCode = 1;
}

const tools = [];
const listeners = [];
const ctx = {
  on(name, handler) {
    listeners.push({ name, handler });
    return () => {};
  },
  tools: {
    register(tool) {
      tools.push(tool);
    },
  },
};

const home = await mkdtemp(join(tmpdir(), "dshmem-host-"));
try {
  await plugin.apply(ctx, { memoryHome: home });

  const names = tools.map((tool) => tool.name);
  const missing = EXPECTED.filter((name) => !names.includes(name));
  if (missing.length > 0) fail(`未注册的工具: ${missing.join(", ")}（实际: ${names.join(", ")}）`);
  if (plugin.name !== "memory") fail(`plugin.name 应为 memory，实际 ${String(plugin.name)}`);
  for (const service of ["tools", "sessions"]) {
    if (!Array.isArray(plugin.inject) || !plugin.inject.includes(service)) {
      fail(`plugin.inject 缺少 ${service}`);
    }
  }
  if (!listeners.some((entry) => entry.name === "session/event")) fail("未订阅 session/event（自动捕获失效）");

  // 真跑一次：工具 → Python → mem.py → SQLite
  const add = tools.find((tool) => tool.name === "memory_add");
  const search = tools.find((tool) => tool.name === "memory_search");
  const added = await add.execute({ title: "宿主冒烟测试", summary: "结论:宿主可用", tags: "smoke" }, {});
  if (!String(added.output).startsWith("ok:")) fail(`memory_add 输出异常: ${added.output}`);
  const found = await search.execute({ query: "宿主冒烟测试" }, {});
  if (!String(found.output).includes("宿主冒烟测试")) fail(`memory_search 没检索到刚写入的记忆: ${found.output}`);

  if (process.exitCode !== 1) {
    console.log(`PASS: DSH 运行时 ${process.version} 上加载正常`);
    console.log(`      工具: ${names.join(", ")}`);
    console.log(`      memory_add → ${String(added.output).trim()}`);
    console.log(`      memory_search → ${String(found.output).trim().split("\n")[0]}`);
    console.log(`      dsh-tools: ${toolsDir}`);
  }
} finally {
  await rm(home, { recursive: true, force: true });
}
