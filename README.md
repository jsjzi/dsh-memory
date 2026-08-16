# dsh-memory — 全局记忆插件

把对话提炼为结构化记忆（结论/决定/待办 + 标签），SQLite FTS5 中文全文检索，Top-K 注入上下文。
基于 [mem.py](../..) 双层记忆系统，插件自包含（mem.py 随包分发，零第三方依赖）。

## 安装

```bash
# 在 profile 里注册本地插件：
# 1. 把本目录放到 profiles/web/vendor-plugins/dsh-memory
# 2. profiles/web/package.json 的 dependencies 加:
#    "dsh-memory": "link:vendor-plugins\\dsh-memory"
# 3. profiles/web/package.json 的 dsh.profile.bundles 加: "dsh-memory"
# 4. cd profiles/web && pnpm install
# 5. 重启 dsh web
```

## 工具

| 工具 | 作用 |
|---|---|
| memory_add | 写入记忆（结构化摘要 + 标签，自动进时间树和索引） |
| memory_search | 两阶段检索（摘要层→全文层），Top-K |
| memory_inject | 生成可直接注入上下文的记忆块（常驻层 + Top-K） |
| memory_remove | 删除记忆（软删除可恢复 / hard 永久） |
| memory_list | 按时间列出记忆 |
| memory_mem | 管理常驻层 MEMORY.md（show/add/rm/clear） |

## 配置

```yaml
# cordis.patch.yml
- id: memory
  name: dsh-memory
  config:
    memoryHome: ""   # 记忆库根，默认 $DSH_HOME/memories
    memPyPath: ""    # mem.py 路径，默认随包
```

## 存储结构

```
memories/
├── MEMORY.md          # 常驻层：高频事实（≤2200 字符，会话开始注入）
├── index.sqlite       # 检索层：SQLite FTS5（trigram 中文分词）
├── notes/年/月/       # 存档层：时间树，每文件 frontmatter 含 date/title/tags/summary
├── notes/rollup/      # 月度聚合
└── .trash/            # 软删除回收站
```

## Token 纪律

存储与检索 0 token；注入有上限（Top-K ≤3，约 2k token）。记忆系统是检索系统，不是上下文转储系统。

## License

MIT