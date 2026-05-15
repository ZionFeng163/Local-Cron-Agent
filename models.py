"""
Task 数据模型 + SQLite 持久化层
所有任务（内置心跳 + 沙盒 crontab）统一存储于 agent_data/tasks.db
"""
import sqlite3
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple

DB_PATH = os.path.join(os.path.dirname(__file__), "agent_data", "tasks.db")


@dataclass
class Task:
    id: str
    name: str
    source: str              # "internal" | "sandbox"
    cron_expr: str           # cron 表达式，内置任务为 interval 描述
    script_path: str         # 沙盒中受平台管理的 .sh 脚本路径
    status: str              # "running" | "paused" | "syncing"
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    last_synced_at: str = ""
    monitor_enabled: int = 1
    consecutive_failures: int = 0
    last_run_at: str = ""
    last_success_at: str = ""
    last_exit_code: Optional[int] = None
    last_auto_heal_at: str = ""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库，创建/迁移 tasks 与 task_runs 表"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = _connect()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            source TEXT NOT NULL,
            cron_expr TEXT DEFAULT '',
            script_path TEXT DEFAULT '',
            status TEXT DEFAULT 'running',
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            last_synced_at TEXT DEFAULT '',
            monitor_enabled INTEGER DEFAULT 1,
            consecutive_failures INTEGER DEFAULT 0,
            last_run_at TEXT DEFAULT '',
            last_success_at TEXT DEFAULT '',
            last_exit_code INTEGER,
            last_auto_heal_at TEXT DEFAULT ''
        )
    """)

    # 向后兼容迁移：老库没有新增列时补齐
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    alter_specs = {
        "script_path": "ALTER TABLE tasks ADD COLUMN script_path TEXT DEFAULT ''",
        "monitor_enabled": "ALTER TABLE tasks ADD COLUMN monitor_enabled INTEGER DEFAULT 1",
        "consecutive_failures": "ALTER TABLE tasks ADD COLUMN consecutive_failures INTEGER DEFAULT 0",
        "last_run_at": "ALTER TABLE tasks ADD COLUMN last_run_at TEXT DEFAULT ''",
        "last_success_at": "ALTER TABLE tasks ADD COLUMN last_success_at TEXT DEFAULT ''",
        "last_exit_code": "ALTER TABLE tasks ADD COLUMN last_exit_code INTEGER",
        "last_auto_heal_at": "ALTER TABLE tasks ADD COLUMN last_auto_heal_at TEXT DEFAULT ''",
    }
    for col, sql in alter_specs.items():
        if col not in existing_cols:
            conn.execute(sql)
    if "command" in existing_cols and "script_path" in existing_cols:
        conn.execute(
            "UPDATE tasks SET script_path = command WHERE source = 'sandbox' AND IFNULL(script_path, '') = ''"
        )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_runs (
            run_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            run_at TEXT DEFAULT '',
            exit_code INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success',
            output_tail TEXT DEFAULT '',
            duration_ms INTEGER DEFAULT 0,
            source TEXT DEFAULT 'cron'
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_heal_records (
            heal_id TEXT PRIMARY KEY,
            task_id TEXT DEFAULT '',
            trigger TEXT DEFAULT '',
            category TEXT DEFAULT '',
            action TEXT DEFAULT '',
            ok INTEGER DEFAULT 0,
            message TEXT DEFAULT '',
            exit_code INTEGER,
            failures INTEGER,
            created_at TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_heal_created_at ON task_heal_records(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_heal_task_created ON task_heal_records(task_id, created_at DESC)")

    conn.commit()
    conn.close()


def _row_to_task(row: sqlite3.Row) -> Task:
    return Task(
        id=row["id"],
        name=row["name"],
        source=row["source"],
        cron_expr=row["cron_expr"],
        script_path=row["script_path"] or "",
        status=row["status"],
        description=row["description"] or "",
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
        last_synced_at=row["last_synced_at"] or "",
        monitor_enabled=int(row["monitor_enabled"] or 1),
        consecutive_failures=int(row["consecutive_failures"] or 0),
        last_run_at=row["last_run_at"] or "",
        last_success_at=row["last_success_at"] or "",
        last_exit_code=row["last_exit_code"],
        last_auto_heal_at=row["last_auto_heal_at"] or "",
    )


def get_all_tasks(source: Optional[str] = None) -> List[Task]:
    conn = _connect()
    if source:
        rows = conn.execute("SELECT * FROM tasks WHERE source = ? ORDER BY created_at", (source,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks ORDER BY source, created_at").fetchall()
    conn.close()
    return [_row_to_task(r) for r in rows]


def get_task(task_id: str) -> Optional[Task]:
    conn = _connect()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return _row_to_task(row) if row else None


def upsert_task(task: Task):
    """插入或更新任务"""
    conn = _connect()
    conn.execute("""
        INSERT INTO tasks (
            id, name, source, cron_expr, script_path, status, description,
            created_at, updated_at, last_synced_at,
            monitor_enabled, consecutive_failures, last_run_at,
            last_success_at, last_exit_code, last_auto_heal_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            cron_expr=excluded.cron_expr,
            script_path=excluded.script_path,
            status=excluded.status,
            description=excluded.description,
            updated_at=excluded.updated_at,
            last_synced_at=excluded.last_synced_at,
            monitor_enabled=excluded.monitor_enabled,
            consecutive_failures=excluded.consecutive_failures,
            last_run_at=excluded.last_run_at,
            last_success_at=excluded.last_success_at,
            last_exit_code=excluded.last_exit_code,
            last_auto_heal_at=excluded.last_auto_heal_at
    """, (
        task.id, task.name, task.source, task.cron_expr, task.script_path,
        task.status, task.description, task.created_at, task.updated_at, task.last_synced_at,
        task.monitor_enabled, task.consecutive_failures, task.last_run_at,
        task.last_success_at, task.last_exit_code, task.last_auto_heal_at
    ))
    conn.commit()
    conn.close()


def update_task_status(task_id: str, status: str):
    conn = _connect()
    conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), task_id))
    conn.commit()
    conn.close()


def update_task_runtime(task_id: str, run_at: str, exit_code: int) -> int:
    """更新任务最近运行状态并返回最新 consecutive_failures"""
    conn = _connect()
    row = conn.execute(
        "SELECT consecutive_failures FROM tasks WHERE id = ?",
        (task_id,)
    ).fetchone()
    if not row:
        conn.close()
        return 0

    prev_failures = int(row["consecutive_failures"] or 0)
    success = exit_code == 0
    new_failures = 0 if success else prev_failures + 1

    if success:
        conn.execute(
            """
            UPDATE tasks
            SET last_run_at = ?, last_success_at = ?, last_exit_code = ?,
                consecutive_failures = ?, updated_at = ?
            WHERE id = ?
            """,
            (run_at, run_at, exit_code, new_failures, _now(), task_id)
        )
    else:
        conn.execute(
            """
            UPDATE tasks
            SET last_run_at = ?, last_exit_code = ?,
                consecutive_failures = ?, updated_at = ?
            WHERE id = ?
            """,
            (run_at, exit_code, new_failures, _now(), task_id)
        )

    conn.commit()
    conn.close()
    return new_failures


def mark_task_auto_heal(task_id: str, at_time: Optional[str] = None):
    conn = _connect()
    conn.execute(
        "UPDATE tasks SET last_auto_heal_at = ?, updated_at = ? WHERE id = ?",
        (at_time or _now(), _now(), task_id)
    )
    conn.commit()
    conn.close()


def delete_task(task_id: str):
    conn = _connect()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.execute("DELETE FROM task_runs WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM task_heal_records WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()


def delete_tasks_by_source(source: str):
    conn = _connect()
    task_ids = [r["id"] for r in conn.execute("SELECT id FROM tasks WHERE source = ?", (source,)).fetchall()]
    conn.execute("DELETE FROM tasks WHERE source = ?", (source,))
    if task_ids:
        conn.executemany("DELETE FROM task_runs WHERE task_id = ?", [(tid,) for tid in task_ids])
        conn.executemany("DELETE FROM task_heal_records WHERE task_id = ?", [(tid,) for tid in task_ids])
    conn.commit()
    conn.close()


def insert_task_run(run_id: str, task_id: str, run_at: str, exit_code: int,
                    status: str, output_tail: str = "", duration_ms: int = 0,
                    source: str = "cron") -> bool:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO task_runs (run_id, task_id, run_at, exit_code, status, output_tail, duration_ms, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, task_id, run_at, exit_code, status, output_tail, duration_ms, source)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def get_recent_task_runs(task_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    conn = _connect()
    rows = conn.execute(
        """
        SELECT run_id, task_id, run_at, exit_code, status, output_tail, duration_ms, source
        FROM task_runs
        WHERE task_id = ?
        ORDER BY run_at DESC
        LIMIT ?
        """,
        (task_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def insert_task_heal_record(heal_id: str, task_id: str, trigger: str, category: str, action: str,
                            ok: bool, message: str = "", exit_code: Optional[int] = None,
                            failures: Optional[int] = None, created_at: Optional[str] = None):
    conn = _connect()
    conn.execute(
        """
        INSERT INTO task_heal_records (
            heal_id, task_id, trigger, category, action, ok, message, exit_code, failures, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            heal_id,
            task_id,
            trigger,
            category,
            action,
            1 if ok else 0,
            message,
            exit_code,
            failures,
            created_at or _now(),
        )
    )
    conn.commit()
    conn.close()


def _build_heal_filters(task_id: str = "", trigger: str = "", category: str = "", action: str = "",
                        ok: Optional[int] = None) -> Tuple[str, List[Any]]:
    where_parts = []
    params: List[Any] = []

    if task_id:
        where_parts.append("task_id = ?")
        params.append(task_id)
    if trigger:
        where_parts.append("trigger = ?")
        params.append(trigger)
    if category:
        where_parts.append("category = ?")
        params.append(category)
    if action:
        where_parts.append("action = ?")
        params.append(action)
    if ok is not None:
        where_parts.append("ok = ?")
        params.append(int(ok))

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    return where_sql, params


def get_task_heal_records(limit: int = 50, offset: int = 0, task_id: str = "", trigger: str = "",
                          category: str = "", action: str = "", ok: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = _connect()
    where_sql, params = _build_heal_filters(task_id, trigger, category, action, ok)

    rows = conn.execute(
        f"""
        SELECT heal_id, task_id, trigger, category, action, ok, message, exit_code, failures, created_at
        FROM task_heal_records
        {where_sql}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_task_heal_records(task_id: str = "", trigger: str = "", category: str = "", action: str = "",
                            ok: Optional[int] = None) -> int:
    conn = _connect()
    where_sql, params = _build_heal_filters(task_id, trigger, category, action, ok)

    row = conn.execute(
        f"SELECT COUNT(*) as total FROM task_heal_records {where_sql}",
        params
    ).fetchone()
    conn.close()
    return int(row["total"] if row else 0)


def gen_id() -> str:
    return uuid.uuid4().hex[:12]
