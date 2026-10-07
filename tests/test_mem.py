#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mem.py 单元测试（纯标准库，无第三方依赖）。

运行:
  python tests/test_mem.py -v
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MEM_PY = Path(__file__).resolve().parent.parent / "mem.py"
PY = sys.executable


def run_mem(home: Path, *args: str) -> str:
    # 强制子进程用 UTF-8：Windows 中文环境下 Python 默认 GBK，测试按 UTF-8 解码
    # stderr 会直接抛 UnicodeDecodeError（也就是「中文字符断言」失败的根因）。
    env = dict(os.environ, MEMORY_HOME=str(home), PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8")
    r = subprocess.run([PY, str(MEM_PY), *args], capture_output=True,
                       text=True, encoding="utf-8", env=env, timeout=60)
    if r.returncode != 0:
        raise AssertionError(f"mem.py {' '.join(args)} 失败 rc={r.returncode}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def sqlite_rows(home: Path, sql: str):
    conn = sqlite3.connect(home / "index.sqlite")
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


class MemTestCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="memtest_"))
        run_mem(self.home, "init")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)


class TestInit(MemTestCase):
    def test_init_creates_structure(self):
        self.assertTrue((self.home / "notes").is_dir())
        self.assertTrue((self.home / "notes" / "rollup").is_dir())
        self.assertTrue((self.home / ".trash").is_dir())
        self.assertTrue((self.home / "index.sqlite").is_file())

    def test_init_creates_tables(self):
        tables = {r[0] for r in sqlite_rows(self.home, "SELECT name FROM sqlite_master")}
        self.assertIn("mem", tables)
        self.assertIn("mem_vec", tables)
        self.assertIn("mem_fts", tables)


class TestAdd(MemTestCase):
    def test_add_writes_file_with_frontmatter(self):
        run_mem(self.home, "add", "项目立项", "-t", "项目,技术", "-s", "结论:用Python; 决定:采用FastAPI", "--date", "2026-08-01")
        f = self.home / "notes" / "2026" / "08" / "01-项目立项.md"
        self.assertTrue(f.is_file(), "记忆文件未按时间树落盘")
        content = f.read_text(encoding="utf-8")
        self.assertIn("date: 2026-08-01", content)
        self.assertIn("title: 项目立项", content)
        self.assertIn("tags: 项目,技术", content)
        self.assertIn("summary: 结论:用Python; 决定:采用FastAPI", content)

    def test_add_indexes_into_sqlite_and_fts(self):
        run_mem(self.home, "add", "会议纪要", "-s", "决定:下周发布", "--date", "2026-08-02")
        rows = sqlite_rows(self.home, "SELECT count(*) FROM mem")
        self.assertEqual(rows[0][0], 1)
        rows = sqlite_rows(self.home, "SELECT count(*) FROM mem_fts")
        self.assertEqual(rows[0][0], 1)

    def test_add_without_date_uses_default(self):
        run_mem(self.home, "add", "临时笔记", "-s", "随手记")
        f = next((self.home / "notes").rglob("*.md"))
        self.assertIn("date:", f.read_text(encoding="utf-8"))


class TestSearch(MemTestCase):
    def setUp(self):
        super().setUp()
        run_mem(self.home, "add", "数据库选型", "-s", "结论:选PostgreSQL", "--date", "2026-08-01")
        run_mem(self.home, "add", "前端框架", "-s", "结论:选React", "--date", "2026-08-02")

    def test_search_hits_summary_layer(self):
        out = run_mem(self.home, "search", "PostgreSQL")
        self.assertIn("数据库选型", out)

    def test_search_no_result(self):
        out = run_mem(self.home, "search", "不存在的关键词xyz")
        self.assertIn("无结果", out)

    def test_search_body_fallback(self):
        run_mem(self.home, "add", "隐藏细节", "-s", "无摘要", "--content", "正文里有独角兽这个词", "--date", "2026-08-03")
        out = run_mem(self.home, "search", "独角兽")
        self.assertIn("隐藏细节", out)


class TestInject(MemTestCase):
    def setUp(self):
        super().setUp()
        for i in range(5):
            run_mem(self.home, "add", f"记忆条目{i}", "-s", f"关于主题{i}的内容", "--date", f"2026-08-0{i + 1}")

    def test_inject_default_k_is_3(self):
        """token 纪律：不传 -k 时默认只注入 3 条"""
        out = run_mem(self.home, "inject", "主题")
        n = out.count("- [")
        self.assertLessEqual(n, 3, f"默认注入应 ≤3 条，实际 {n}:\n{out}")

    def test_inject_explicit_k(self):
        out = run_mem(self.home, "inject", "主题", "-k", "2")
        self.assertLessEqual(out.count("- ["), 2)

    def test_inject_includes_resident_layer(self):
        run_mem(self.home, "mem", "add", "老板偏好: 回复要简练")
        out = run_mem(self.home, "inject", "主题")
        self.assertIn("老板偏好", out)


class TestRm(MemTestCase):
    def test_rm_soft_moves_to_trash(self):
        run_mem(self.home, "add", "要删除的", "-s", "临时", "--date", "2026-08-01")
        f = self.home / "notes" / "2026" / "08" / "01-要删除的.md"
        run_mem(self.home, "rm", "notes/2026/08/01-要删除的.md")
        self.assertFalse(f.exists(), "软删除后原文件应移走")
        self.assertTrue(list((self.home / ".trash").iterdir()), "回收站应有文件")
        self.assertEqual(sqlite_rows(self.home, "SELECT count(*) FROM mem")[0][0], 0)

    def test_restore_recovers_file_and_index(self):
        run_mem(self.home, "add", "要恢复的", "-s", "重要", "--date", "2026-08-01")
        run_mem(self.home, "rm", "notes/2026/08/01-要恢复的.md")
        name = list((self.home / ".trash").iterdir())[0].name
        run_mem(self.home, "restore", name)
        self.assertTrue((self.home / "notes" / "2026" / "08" / "01-要恢复的.md").exists())
        self.assertEqual(sqlite_rows(self.home, "SELECT count(*) FROM mem")[0][0], 1)

    def test_rm_hard_deletes_permanently(self):
        run_mem(self.home, "add", "彻底删除", "-s", "敏感", "--date", "2026-08-01")
        run_mem(self.home, "rm", "notes/2026/08/01-彻底删除.md", "--hard")
        self.assertFalse((self.home / "notes" / "2026" / "08" / "01-彻底删除.md").exists())
        self.assertFalse(list((self.home / ".trash").iterdir()), "hard 删除不应进回收站")


class TestMemResident(MemTestCase):
    def test_mem_add_show_rm_clear(self):
        run_mem(self.home, "mem", "add", "事实A: 项目代号X")
        run_mem(self.home, "mem", "add", "事实B: 周会周三")
        out = run_mem(self.home, "mem", "show")
        self.assertIn("事实A", out)
        self.assertIn("事实B", out)
        run_mem(self.home, "mem", "rm", "事实A")
        out = run_mem(self.home, "mem", "show")
        self.assertNotIn("事实A", out)
        self.assertIn("事实B", out)
        run_mem(self.home, "mem", "clear")
        self.assertEqual(run_mem(self.home, "mem", "show").strip().splitlines()[0], "MEMORY.md [0/2200 chars (0%)]")

    def test_mem_limit_rejects_oversize(self):
        big = "字" * 2500
        r = subprocess.run([PY, str(MEM_PY), "mem", "add", big], capture_output=True,
                           text=True, encoding="utf-8", env=dict(os.environ, MEMORY_HOME=str(self.home)))
        self.assertNotEqual(r.returncode, 0, "超限应拒绝")
        self.assertIn("超限", r.stderr)

    def test_mem_rm_requires_unique_match(self):
        run_mem(self.home, "mem", "add", "重复前缀A")
        run_mem(self.home, "mem", "add", "重复前缀B")
        r = subprocess.run([PY, str(MEM_PY), "mem", "rm", "重复前缀"], capture_output=True,
                           text=True, encoding="utf-8", env=dict(os.environ, MEMORY_HOME=str(self.home)))
        self.assertNotEqual(r.returncode, 0, "非唯一匹配应拒绝")


class TestListRollupIndex(MemTestCase):
    def test_list_by_month(self):
        run_mem(self.home, "add", "八月条目", "-s", "A", "--date", "2026-08-01")
        run_mem(self.home, "add", "七月条目", "-s", "B", "--date", "2026-07-15")
        out = run_mem(self.home, "list", "2026-08")
        self.assertIn("八月条目", out)
        self.assertNotIn("七月条目", out)

    def test_rollup_creates_file(self):
        run_mem(self.home, "add", "月度素材", "-s", "C", "--date", "2026-08-01")
        run_mem(self.home, "rollup", "2026-08")
        f = self.home / "notes" / "rollup" / "2026-08.md"
        self.assertTrue(f.is_file())
        self.assertIn("月度素材", f.read_text(encoding="utf-8"))

    def test_index_rebuild(self):
        run_mem(self.home, "add", "重建前的", "-s", "D", "--date", "2026-08-01")
        (self.home / "index.sqlite").unlink()  # 模拟索引丢失
        run_mem(self.home, "index")
        rows = sqlite_rows(self.home, "SELECT count(*) FROM mem")
        self.assertEqual(rows[0][0], 1)
        idx = self.home / "notes" / "2026" / "08" / "_index.md"
        self.assertTrue(idx.is_file())
        self.assertIn("重建前的", idx.read_text(encoding="utf-8"))


class TestPending(MemTestCase):
    def test_pending_empty_message(self):
        out = run_mem(self.home, "pending")
        self.assertIn("无待整理缓冲", out)


class TestFirstUse(unittest.TestCase):
    """全新机器（库目录还不存在、也没跑过 init）时不许崩。

    回归用例：db() 以前不建目录，首次直接 search 会抛
    sqlite3.OperationalError: unable to open database file。
    """

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="memfresh_"))
        shutil.rmtree(self.home, ignore_errors=True)   # 故意让库目录不存在
        self.addCleanup(lambda: shutil.rmtree(self.home, ignore_errors=True))

    def test_search_before_init(self):
        self.assertIn("无结果", run_mem(self.home, "search", "任意关键词"))
        self.assertTrue((self.home / "index.sqlite").is_file())

    def test_mem_add_before_init(self):
        self.assertIn("已写入", run_mem(self.home, "mem", "add", "新机器上的第一条常驻记忆"))

    def test_add_before_init(self):
        run_mem(self.home, "add", "全新库里的第一条", "-s", "结论:可用")
        self.assertIn("全新库里的第一条", run_mem(self.home, "search", "全新库"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
