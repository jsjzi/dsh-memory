#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mem.py - 树状双层记忆系统（零第三方依赖，仅 Python 标准库）

层次:
  MEMORY.md    常驻层: 高频事实, <=2200 字符, 会话开始时注入
  notes/       存档层: notes/YYYY/MM/DD-标题.md, 每目录 _index.md
  index.sqlite 检索层: SQLite FTS5(trigram) 中文全文搜索
  .trash/      软删除回收站

用法:
  mem.py init
  mem.py add <标题> [-t 标签] [-s 摘要] [--content 正文] [--append] [--date 日期]
  mem.py search <关键词> [-k N]
  mem.py get <路径>             # 读取完整记忆
  mem.py list [YYYY-MM]         # 按时间列出
  mem.py inject <关键词> [-k N]  # 生成可注入上下文的 Top-K 块
  mem.py rm <路径> [--hard]     # 软删除(.trash) / 永久删除
  mem.py trash                  # 列出回收站
  mem.py restore <文件名>       # 从回收站恢复
  mem.py mem show|add|rm|clear  # 常驻层 MEMORY.md 管理
  mem.py index                  # 重建索引 + 重新生成 _index.md
  mem.py rollup [YYYY-MM]       # 月度聚合摘要
"""
import argparse, os, re, shutil, sqlite3, sys, time
from datetime import datetime
from pathlib import Path

MEM_LIMIT = 2200          # MEMORY.md 字符上限(约800 token)
SEP = "\n§\n"             # MEMORY.md 条目分隔符(同 Hermes)
TRASH_DIR = ".trash"

def home() -> Path:
    """记忆库根: MEMORY_HOME > $DSH_HOME/memories(全局) > mem.py 所在目录(项目级)"""
    env = os.environ.get("MEMORY_HOME")
    if env:
        return Path(env)
    dsh = os.environ.get("DSH_HOME")
    if dsh:
        return Path(dsh) / "memories"
    return Path(__file__).resolve().parent / "memories"

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")

# ---------- 文件/正文 ----------

def parse_frontmatter(text: str):
    """返回 (meta: dict, body: str)。meta 仅保留已知键。"""
    meta = {}
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end > 0:
            for line in text[4:end].splitlines():
                m = re.match(r"^(\w+):\s*(.*)$", line)
                if m and m.group(1) in ("date", "title", "tags", "summary", "decisions"):
                    meta[m.group(1)] = m.group(2).strip()
            return meta, text[end + 5:].lstrip("\n")
    return meta, text

def dump_frontmatter(meta: dict, body: str) -> str:
    lines = ["---"]
    for k in ("date", "title", "tags", "summary", "decisions"):
        if meta.get(k):
            lines.append(f"{k}: {meta[k]}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n" + body.rstrip() + "\n"

def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)

def slugify(title: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\s]+', "-", title).strip("-")
    return s[:60] or "untitled"

# ---------- 索引 ----------

def db(root: Path):
    conn = sqlite3.connect(root / "index.sqlite")
    conn.execute("""CREATE TABLE IF NOT EXISTS mem(
        path TEXT PRIMARY KEY, date TEXT, title TEXT, tags TEXT, summary TEXT, body TEXT)""")
    try:
        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
            path UNINDEXED, date, title, summary, body, tokenize='trigram')""")
    except sqlite3.OperationalError:
        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
            path UNINDEXED, date, title, summary, body)""")
    return conn

def upsert(conn, path: str, meta: dict, body: str):
    conn.execute("INSERT OR REPLACE INTO mem VALUES (?,?,?,?,?,?)",
                 (path, meta.get("date", ""), meta.get("title", ""),
                  meta.get("tags", ""), meta.get("summary", ""), body))
    conn.execute("DELETE FROM mem_fts WHERE path=?", (path,))
    conn.execute("INSERT INTO mem_fts VALUES (?,?,?,?,?)",
                 (path, meta.get("date", ""), meta.get("title", ""),
                  meta.get("summary", ""), body))
    conn.commit()

def remove_path(conn, path: str):
    conn.execute("DELETE FROM mem WHERE path=?", (path,))
    conn.execute("DELETE FROM mem_fts WHERE path=?", (path,))
    conn.commit()

# ---------- 常驻层 MEMORY.md ----------

def read_memfile(root: Path) -> str:
    f = root / "MEMORY.md"
    return f.read_text(encoding="utf-8") if f.exists() else ""

def write_memfile(root: Path, content: str):
    atomic_write(root / "MEMORY.md", content)

def mem_entries(content: str):
    return [e.strip() for e in content.split(SEP) if e.strip()]

def mem_show(root):
    c = read_memfile(root)
    print(f"MEMORY.md [{len(c)}/{MEM_LIMIT} chars ({len(c) * 100 // MEM_LIMIT}%)]")
    print("---")
    for i, e in enumerate(mem_entries(c), 1):
        print(f"{i}. {e}")

def mem_add(root, text):
    entries = mem_entries(read_memfile(root)) + [text]
    new = SEP.join(entries)
    if len(new) > MEM_LIMIT:
        sys.exit(f"error: 超限 {len(new)}/{MEM_LIMIT}。请先 mem rm 或让模型合并旧条目后再 add。")
    write_memfile(root, new)
    print("ok: 已写入 MEMORY.md")

def mem_rm(root, sub):
    c = read_memfile(root)
    hits = [e for e in mem_entries(c) if sub in e]
    if len(hits) != 1:
        sys.exit(f"error: 匹配到 {len(hits)} 条, 需要唯一子串。\n" + "\n".join(f"- {h}" for h in hits[:5]))
    rest = [e for e in mem_entries(c) if e != hits[0]]
    write_memfile(root, SEP.join(rest))
    print("ok: 已删除条目")

# ---------- 命令 ----------

def cmd_init(root):
    for d in ("notes", "notes/rollup", TRASH_DIR):
        (root / d).mkdir(parents=True, exist_ok=True)
    db(root)
    print(f"ok: 记忆库已初始化于 {root}")

def cmd_add(root, args):
    date = args.date or now()
    y, m, d = date[:4], date[5:7], date[8:10]
    fname = f"{d}-{slugify(args.title)}.md"
    path = root / "notes" / y / m / fname
    body = (args.content or "").strip()
    if not body and not sys.stdin.isatty():
        body = sys.stdin.read().strip()
    if args.append and path.exists():
        meta, old = parse_frontmatter(path.read_text(encoding="utf-8"))
        meta["date"] = date
        meta["title"] = args.title
        if args.tags: meta["tags"] = args.tags
        if args.summary: meta["summary"] = args.summary
        body = old.rstrip() + "\n\n" + body
        atomic_write(path, dump_frontmatter(meta, body))
    else:
        meta = {"date": date, "title": args.title}
        if args.tags: meta["tags"] = args.tags
        if args.summary: meta["summary"] = args.summary
        if not body: body = args.title
        atomic_write(path, dump_frontmatter(meta, body))
    upsert(db(root), str(path.relative_to(root)).replace("\\", "/"), meta, body)
    print(f"ok: {path}")
    return path

def search_rows(conn, query: str, k: int, phase: str):
    """phase='summary' 只搜标题+摘要; 'full' 搜全部; 短查询(<3字符)退回 LIKE。"""
    if len(query) >= 3:
        if phase == "summary":
            sql = "SELECT path, date, title, tags, summary FROM mem_fts WHERE mem_fts MATCH ? ORDER BY rank LIMIT ?"
            q = '{title summary} : "' + query + '"'
        else:
            sql = "SELECT path, date, title, tags, summary FROM mem_fts WHERE mem_fts MATCH ? ORDER BY rank LIMIT ?"
            q = '"' + query + '"'
        try:
            return [dict(zip(("path", "date", "title", "tags", "summary"), r))
                    for r in conn.execute(sql, (q, k))]
        except sqlite3.OperationalError:
            pass
    like = f"%{query}%"
    col = "title" if phase == "summary" else "body"
    sql = f"SELECT path, date, title, tags, summary FROM mem WHERE {col} LIKE ? LIMIT ?"
    return [dict(zip(("path", "date", "title", "tags", "summary"), r))
            for r in conn.execute(sql, (like, k))]

def cmd_search(root, args):
    conn = db(root)
    hits = search_rows(conn, args.query, args.k, "summary")
    if len(hits) < args.k:
        full = search_rows(conn, args.query, args.k, "full")
        seen = {h["path"] for h in hits}
        hits += [h for h in full if h["path"] not in seen][: args.k - len(hits)]
    if not hits:
        print("(无结果)")
        return
    for i, h in enumerate(hits, 1):
        print(f"[{i}] {h['date']}  {h['title']}  tags: {h['tags']}")
        print(f"    摘要: {h['summary'] or '(无)'}")
        print(f"    文件: {h['path']}")

def cmd_get(root, args):
    p = Path(args.path)
    if not p.is_absolute():
        p = root / p
    if not p.exists():
        sys.exit(f"error: 文件不存在 {p}")
    print(p.read_text(encoding="utf-8"))

def cmd_list(root, args):
    y, m = (args.month[:4], args.month[5:7]) if args.month else ("", "")
    conn = db(root)
    rows = conn.execute("SELECT date, title, summary FROM mem WHERE date LIKE ? ORDER BY date",
                        (f"{y}-{m}-%" if y else "%-%",)).fetchall()
    if not rows:
        print("(无)")
        return
    for d, t, s in rows:
        print(f"{d}  {t}  |  {s or ''}")

def cmd_inject(root, args):
    out = []
    c = read_memfile(root)
    if c:
        out.append(f"<memory 常驻层 {len(c)}字符>")
        out += [f"- {e}" for e in mem_entries(c)]
        out.append("</memory>")
    conn = db(root)
    hits = search_rows(conn, args.query, args.k, "summary")
    if len(hits) < args.k:
        full = search_rows(conn, args.query, args.k, "full")
        seen = {h["path"] for h in hits}
        hits += [h for h in full if h["path"] not in seen][: args.k - len(hits)]
    if hits:
        out.append(f"<memory 检索 Top-{len(hits)} 关键词:{args.query}>")
        for h in hits:
            out.append(f"- [{h['date']}] {h['title']}: {h['summary'] or '(无摘要,见正文)'}")
        out.append("</memory>")
    print("\n".join(out) if out else "(无结果)")

def cmd_rm(root, args):
    p = Path(args.path)
    if not p.is_absolute():
        p = root / p
    if not p.exists():
        sys.exit(f"error: 文件不存在 {p}")
    rel = str(p.relative_to(root)).replace("\\", "/")
    if args.hard:
        p.unlink()
        print(f"ok: 已永久删除 {rel}")
    else:
        tr = root / TRASH_DIR
        tr.mkdir(exist_ok=True)
        target = tr / (rel.replace("/", "__") + f".{int(time.time())}")
        shutil.move(str(p), str(target))
        print(f"ok: 已移入回收站 {target.name} (mem.py restore {target.name} 可恢复)")
    remove_path(db(root), rel)
    for d in list(p.parents):
        if d != root and d.is_dir() and not any(d.iterdir()):
            try: d.rmdir()
            except OSError: pass

def cmd_trash(root):
    tr = root / TRASH_DIR
    if not tr.exists():
        print("(空)")
        return
    for f in sorted(tr.iterdir()):
        orig = f.name.rsplit(".", 1)[0].replace("__", "/")
        print(f"{f.name}  <-  {orig}")

def cmd_restore(root, args):
    tr = root / TRASH_DIR
    f = tr / args.name
    if not f.exists():
        sys.exit(f"error: 回收站无 {args.name}")
    orig = f.name.rsplit(".", 1)[0].replace("__", "/")
    dst = root / orig
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(f), str(dst))
    meta, body = parse_frontmatter(dst.read_text(encoding="utf-8"))
    upsert(db(root), orig, meta, body)
    print(f"ok: 已恢复 {orig}")

def cmd_index(root):
    conn = db(root)
    conn.execute("DELETE FROM mem")
    conn.execute("DELETE FROM mem_fts")
    conn.commit()
    months = {}
    for f in sorted((root / "notes").rglob("*.md")):
        if f.name.startswith("_"):
            continue
        meta, body = parse_frontmatter(f.read_text(encoding="utf-8"))
        if not meta.get("date"):
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        upsert(conn, rel, meta, body)
        months.setdefault(rel[6:13], []).append(meta)
    for ym, metas in sorted(months.items()):
        y, m = ym[:4], ym[5:7]
        lines = [f"# {y}-{m} 记忆索引 ({len(metas)} 条)", ""]
        for x in sorted(metas, key=lambda z: z.get("date", "")):
            lines += [f"## {x.get('date','')}  {x.get('title','')}",
                      f"- 摘要: {x.get('summary','')}", f"- tags: {x.get('tags','')}", ""]
        atomic_write(root / "notes" / y / m / "_index.md", "\n".join(lines))
    print(f"ok: 已索引 {sum(len(v) for v in months.values())} 条, 生成 {len(months)} 个月度索引")

def cmd_rollup(root, args):
    if args.month:
        y, m = args.month[:4], args.month[5:7]
    else:
        y, m = datetime.now().strftime("%Y"), datetime.now().strftime("%m")
    conn = db(root)
    rows = conn.execute("SELECT date, title, summary FROM mem WHERE date LIKE ? ORDER BY date",
                        (f"{y}-{m}-%",)).fetchall()
    if not rows:
        print("(该月无记忆)")
        return
    lines = [f"# {y}-{m} 月度聚合 ({len(rows)} 条)", ""]
    for d, t, s in rows:
        lines.append(f"- {d} **{t}**: {s or '(无摘要)'}")
    out = root / "notes" / "rollup" / f"{y}-{m}.md"
    atomic_write(out, "\n".join(lines) + "\n")
    print(f"ok: {out}")
    print("提示: 请模型把上面的机械列表合并成 3-5 条粗粒度摘要后写回该文件。")

def main():
    ap = argparse.ArgumentParser(prog="mem.py", description="树状双层记忆系统")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    p = sub.add_parser("add")
    p.add_argument("title")
    p.add_argument("-t", "--tags")
    p.add_argument("-s", "--summary")
    p.add_argument("--content")
    p.add_argument("--append", action="store_true")
    p.add_argument("--date")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=5)
    p = sub.add_parser("get")
    p.add_argument("path")
    p = sub.add_parser("list")
    p.add_argument("month", nargs="?", default="")
    p = sub.add_parser("inject")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=5)
    p = sub.add_parser("rm")
    p.add_argument("path")
    p.add_argument("--hard", action="store_true")
    sub.add_parser("trash")
    p = sub.add_parser("restore")
    p.add_argument("name")
    p = sub.add_parser("mem")
    p.add_argument("action", choices=["show", "add", "rm", "clear"])
    p.add_argument("arg", nargs="?")
    sub.add_parser("index")
    p = sub.add_parser("rollup")
    p.add_argument("month", nargs="?", default="")
    args = ap.parse_args()

    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    root = home()
    c = args.cmd
    if c == "init": cmd_init(root)
    elif c == "add": cmd_add(root, args)
    elif c == "search": cmd_search(root, args)
    elif c == "get": cmd_get(root, args)
    elif c == "list": cmd_list(root, args)
    elif c == "inject": cmd_inject(root, args)
    elif c == "rm": cmd_rm(root, args)
    elif c == "trash": cmd_trash(root)
    elif c == "restore": cmd_restore(root, args)
    elif c == "mem":
        if args.action == "show": mem_show(root)
        elif args.action == "add":
            if not args.arg: sys.exit("usage: mem.py mem add <文本>")
            mem_add(root, args.arg)
        elif args.action == "rm":
            if not args.arg: sys.exit("usage: mem.py mem rm <唯一子串>")
            mem_rm(root, args.arg)
        elif args.action == "clear":
            write_memfile(root, "")
            print("ok: MEMORY.md 已清空")
    elif c == "index": cmd_index(root)
    elif c == "rollup": cmd_rollup(root, args)

if __name__ == "__main__":
    main()
