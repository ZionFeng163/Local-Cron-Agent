"""
Task 数据模型 + SQLite 持久化层
所有任务（内置心跳 + 沙盒 crontab）统一存储于 agent_data/tasks.db
"""
import sqlite3
import os
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "agent_data", "tasks.db")


@dataclass
class Task:
    id: str
    name: str
    source: str              # "internal" | "sandbox"
    cron_expr: str           # cron 表达式，内置任务为 interval 描述
    command: str             # 执行命令或脚本路径
    status: str              # "running" | "paused" | "syncing"
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_synced_at: str = ""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    """初始化数据库，创建 tasks 表"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source TEXT NOT NULL,
            cron_expr TEXT DEFAULT '',
            command TEXT DEFAULT '',
            status TEXT DEFAULT 'running',
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            last_synced_at TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()


def _row_to_task(row) -> Task:
    return Task(
        id=row[0], name=row[1], source=row[2],
        cron_expr=row[3], command=row[4], status=row[5],
        description=row[6], created_at=row[7],
        updated_at=row[8], last_synced_at=row[9]
    )


def get_all_tasks(source: Optional[str] = None) -> List[Task]:
    conn = sqlite3.connect(DB_PATH)
    if source:
        rows = conn.execute("SELECT * FROM tasks WHERE source = ? ORDER BY created_at", (source,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks ORDER BY source, created_at").fetchall()
    conn.close()
    return [_row_to_task(r) for r in rows]


def get_task(task_id: str) -> Optional[Task]:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return _row_to_task(row) if row else None


def upsert_task(task: Task):
    """插入或更新任务"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO tasks (id, name, source, cron_expr, command, status, description, created_at, updated_at, last_synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, cron_expr=excluded.cron_expr, command=excluded.command,
            status=excluded.status, description=excluded.description,
            updated_at=excluded.updated_at, last_synced_at=excluded.last_synced_at
    """, (task.id, task.name, task.source, task.cron_expr, task.command,
          task.status, task.description, task.created_at, task.updated_at, task.last_synced_at))
    conn.commit()
    conn.close()


def update_task_status(task_id: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), task_id))
    conn.commit()
    conn.close()


def delete_task(task_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def delete_tasks_by_source(source: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM tasks WHERE source = ?", (source,))
    conn.commit()
    conn.close()


def gen_id() -> str:
    return uuid.uuid4().hex[:12]
