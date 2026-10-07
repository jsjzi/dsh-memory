# dsh-memory — 全局记忆插件

把对话提炼为结构化记忆（结论/决定/待办 + 标签），SQLite FTS5 中文全文检索，按需 Top-K 注入上下文。
基于 `mem.py` 双层记忆系统，插件自包含（`mem.py` 随包分发，纯 Python 标准库、零 pip 依赖）。

> 可选增强（**默认关闭**）：语义向量检索（跨词序/同义词召回）需额外 `pip install fastembed`，
> 并先在联网环境预热一次把模型下下来，再设 `MEMORY_SEMANTIC=1`（或插件 `config.semantic: true`）。
> 关闭时走关键词/FTS5 检索，功能不受影响——之所以默认关闭：模型加载会联网，
> 一旦模型没缓存，`add`/`search` 每次都会被拖到调用方超时（实测踩过这个坑）。

## 环境要求

| 依赖 | 说明 |
|---|---|
| DSH | `>=0.1.0-rc.6 <0.3.0`（见 [兼容性](#兼容性)） |
| Python 3 | 用于执行 `mem.py`。DSH 桌面端自带 Python 3.12；系统装的 Python 3.8+ 也可以 |
| 解释器发现顺序 | `config.pythonPath` → `MEMORY_PYTHON` → `PYTHON` → `python` / `python3` / `py -3` |

**不再写死 `python` 命令**：找不到解释器时，工具调用会明确报「找不到可用的 Python 解释器（已尝试: …）」，
而不是静默失败。所有 `mem.py` 子进程都被强制 UTF-8（`PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`），
中文在 Windows GBK 环境下不会乱码。

## 安装

### 桌面端（Electron 客户端）⚠️ 先看这节

桌面客户端读的是 **`desktop` profile**（`$DSH_HOME/profiles/desktop`，默认 `~/.dsh/profiles/desktop`），
而 CLI **按设计拒绝**任何点名该 profile 的命令（`dsh plugin --profile desktop …`，连 `--dump-config` 也不行）：

```
error: profile "desktop" is managed exclusively by the Electron application
```

所以桌面端只有一条正确路径：**让桌面端里的 DSH 自己装**（在会话里说「装上 dsh-memory」，
由客户端内置的 `plugin_manager` 在当前 profile 里用自带 pnpm 完成安装，并写入 `dsh.profile.bundles`）。
安装规格用 git 地址，不要用裸包名（原因见下方 ⚠️）：

```
git+https://github.com/jsjzi/dsh-memory.git
```

装完重启客户端；新装的包通常先热生效，重启能确保加载。

### Web（`dsh web`，`web` profile）

```bash
dsh plugin --profile web add git+https://github.com/jsjzi/dsh-memory.git
# 装完重启 dsh web，再 F5 刷新
```

也可以用本地目录（开发联调）：

```bash
dsh plugin --profile web add link:D:\Plugins\dsh-memory     # 软链，改源码立即生效
dsh plugin --profile web add file:D:\Plugins\dsh-memory     # 拷贝一份
```

> 手动等价路径（不推荐，profile 目录由客户端管理）：把仓库放到固定目录 →
> 在 profile 的 `package.json` 里加 `"dsh-memory": "link:<绝对路径>"` → `dsh.profile.bundles` 加 `"dsh-memory"` →
> 在该 profile 目录里用 DSH 自带的 pnpm `install`。

### ⚠️ 不要 `dsh plugin add dsh-memory`

npm 上的 [`dsh-memory`](https://www.npmjs.com/package/dsh-memory)（维护者 bbnopromo）是**另一个实现**，
它的 `peerDependencies` 停在 `^0.1.0-rc.6`，在 DSH `0.2.x` 上会被兼容性闸门直接拒绝：

```
dsh: installation rejected: Plugin dsh-memory@0.1.0 is incompatible with dsh 0.2.0-rc.2 ...
```

本仓库**没有发布到 npm**（同名已被占用）。始终用 `git+https://github.com/jsjzi/dsh-memory.git`
或本地 `link:` / `file:` 路径安装。

## 工具

| 工具 | 作用 |
|---|---|
| memory_add | 写入记忆（结构化摘要 + 标签，自动进时间树和索引） |
| memory_search | 两阶段检索（摘要层→全文层），Top-K |
| memory_inject | 生成可直接注入上下文的记忆块（常驻层 + Top-K） |
| memory_remove | 删除记忆（软删除可恢复 / hard 永久） |
| memory_list | 按时间列出记忆 |
| memory_mem | 管理常驻层 MEMORY.md（show/add/rm/clear） |

**注入是「按需」的**：本插件不注册 system-prompt 段，`MEMORY.md` 不会在会话开始自动进上下文；
需要时由模型调用 `memory_inject`（常驻层 + 检索 Top-K）或 `memory_mem show` 读取。

## 自动捕获（默认开启）

每轮用户消息的原文会追加到 `notes/pending/YYYY-MM-DD.md`（防漏记的兜底层，模型可读它整理成正式记忆）。护栏：

- 命中系统注入前缀（`Current runtime context`、`<system-reminder>` 等）→ 跳过；
- 命中凭证类模式（password / api key / bearer / `sk-…` / 验证码）→ 跳过；
- 单条超过 500 字符 → 截断后再落盘。

介意明文落盘的话，先确认 `$DSH_HOME/memories/notes/pending/` 的处理方式，或把 `memoryHome`
指到你想放的目录。

## 配置

```yaml
# cordis.patch.yml
- id: memory
  name: dsh-memory
  config:
    memoryHome: ""     # 记忆库根，默认 $DSH_HOME/memories
    memPyPath: ""      # mem.py 路径，默认随包
    pythonPath: ""     # Python 解释器，默认自动探测
    semantic: false    # true = 打开语义检索（需 fastembed + 已缓存模型）
```

## 存储结构

```
memories/
├── MEMORY.md          # 常驻层：高频事实（≤2200 字符）
├── index.sqlite       # 检索层：SQLite FTS5（trigram 中文分词）
├── notes/年/月/       # 存档层：时间树，每文件 frontmatter 含 date/title/tags/summary
├── notes/pending/     # 自动捕获缓冲（每轮用户消息原文）
├── notes/rollup/      # 月度聚合
└── .trash/            # 软删除回收站
```

## Token 纪律

存储与检索 0 token；注入有上限（`memory_inject` 默认 Top-K = 3，约 2k token，可显式传 `k` 调整）。
记忆系统是检索系统，不是上下文转储系统。

## 兼容性

`package.json` 声明：

```json
"peerDependencies": { "@deepseek-ai/dsh-tools": ">=0.1.0-rc.6 <0.3.0-0" }
```

DSH 安装/启动时会用**运行时版本**去校验 `@deepseek-ai/dsh-*` 声明的范围，所以范围写法有坑：

| 范围 | 0.1.0-rc.8 | 0.2.0-rc.2 | 0.2.5 | 0.3.0-rc.1 |
|---|---|---|---|---|
| `^0.1.0-rc.6`（很多插件在用） | ✅ | ❌ | ❌ | ❌ |
| `>=0.1.0-rc.6 <0.3.0`（漏写 `-0`） | ✅ | ✅ | ✅ | ❌（会被 `includePrerelease` 放进来） |
| **`>=0.1.0-rc.6 <0.3.0-0`** | ✅ | ✅ | ✅ | ❌ |

（`@deepseek-ai/dsh-tools` 是宿主提供的模块，标了 `peerDependenciesMeta.optional`，只是避免 pnpm
尝试去 npm 解析它；DSH 的兼容性校验仍然生效。）

若某个版本确实被闸门挡住而你想临时放行，用**精确版本豁免**（有风险，需明确确认）：

```
dsh plugin --profile <profile> allow-version dsh-memory@<版本> --dsh-version <运行时版本> --accept-risk
```

## 测试

```bash
# 1) mem.py 单元测试（纯标准库，任何 Python 3 都能跑）
python tests/test_mem.py -v

# 2) 宿主半部冒烟测试：用你机器上真实的 DSH 运行时加载插件，并真调用一次工具
cmd /c "set ELECTRON_RUN_AS_NODE=1&& \"<DSH 安装目录>\DeepSeek Harness.exe\" tests\test_host.mjs"
```

第 2 条最能回答「这台机器上到底还能不能装、还能不能跑」：它用 DSH 自带 `app.asar` 里的
`@deepseek-ai/dsh-tools` 真正 `apply()` 插件，检查 6 个工具是否注册成功，再走一遍
`memory_add → memory_search`。找不到 DSH 运行时（例如在普通 node 下）会打印 SKIP 并以 0 退出。

两条测试都会强制子进程 UTF-8；Windows 中文环境（GBK 控制台）下同样通过。

## 变更

### 0.1.2

- 自动捕获护栏补漏：DSH 每轮注入的运行时上下文（`Time sampled while preparing…`、
  `Browser time zone for this request…`、`Elapsed since the preceding…`）实测会作为
  `user/message` 事件到达，原前缀表没覆盖，导致系统噪声被写进 `notes/pending/`；
  现已拦截，并在宿主冒烟测试里加了断言（真实消息必须落、系统注入必须挡）。

### 0.1.1

- 兼容 DSH `0.2.x`：补 `peerDependencies` 范围 `>=0.1.0-rc.6 <0.3.0-0`（含 prerelease 语义）、
  `repository` / `bugs` / `homepage` 元数据；
- **修掉「装上了但一调就卡」**：`mem.py` 的 fastembed 语义检索改为**默认关闭**
  （`MEMORY_SEMANTIC=1` 或 `config.semantic: true` 才启用）。原来只要系统 Python 里装了
  fastembed，`add`/`search` 每次都会尝试联网加载模型，把工具调用拖到超时；
- 只在「完全没传 `--content`」时才读 stdin（插件始终显式传参，并把子进程 stdin 置空），
  避免子进程读空管道阻塞；
- 修掉「首次使用」崩溃：库目录还不存在时直接 `memory_search` 会
  `sqlite3.OperationalError: unable to open database file`，现在 `db()` 先建目录，
  并补 3 条回归测试（`TestFirstUse`）；
- **Windows 中文环境修复**：`mem.py` 的 stdout **和 stderr** 都强制 UTF-8（原先只改 stdout，
  `sys.exit("error: …中文…")` 走 GBK 管道时会让调用方解码失败）；插件侧再补
  `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` 双保险；
- Python 解释器改为多候选探测（`pythonPath` → `MEMORY_PYTHON` → `PYTHON` → `python`/`python3`/`py -3`），
  找不到时报明确错误；
- 新增 `tests/test_host.mjs` 宿主半部冒烟测试；单元测试子进程强制 UTF-8，
  Windows 中文环境下不再出现 `UnicodeDecodeError`；
- README：改写安装说明（桌面端 = `desktop` profile，CLI 拒绝该 profile）、说明 npm 同名包的坑、
  补充自动捕获行为与隐私护栏、语义检索开关。

### 0.1.0

- 首个版本：六个工具 + `mem.py` 双层记忆系统。

## License

MIT
