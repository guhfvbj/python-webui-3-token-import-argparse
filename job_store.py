from __future__ import annotations

import json
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.getenv("REQUEST_TESTER_DATA_DIR") or ROOT / "server_data")
JOBS_FILE = DATA_ROOT / "jobs.json"
MAX_LOG_LINES = int(os.getenv("REQUEST_TESTER_MAX_LOG_LINES", "20000"))
ACTIVE_STATUSES = {"queued", "running", "pause_requested"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


class JobStore:
    def __init__(self, path: Path = JOBS_FILE):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.state = {"jobs": {}}
        self._load()

    def _load(self):
        with self.lock:
            if not self.path.exists():
                return
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                loaded = {"jobs": {}}
            if isinstance(loaded, dict) and isinstance(loaded.get("jobs"), dict):
                self.state = loaded

    def _save_locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, self.path)

    def create_job(
        self,
        *,
        client_id: str,
        target: str,
        input_preview: str,
        course_key: str | None = None,
        course_url: str | None = None,
        token_hash: str | None = None,
    ) -> dict:
        with self.lock:
            job_id = uuid.uuid4().hex
            job = {
                "id": job_id,
                "client_id": client_id,
                "target": target,
                "course_key": course_key,
                "course_url": course_url,
                "token_hash": token_hash,
                "input_preview": input_preview,
                "status": "queued",
                "ok": None,
                "error": None,
                "success_count": 0,
                "fail_count": 0,
                "token_count": 0,
                "created_at": now_iso(),
                "updated_at": now_iso(),
                "started_at": None,
                "finished_at": None,
                "elapsed_seconds": 0,
                "results": [],
                "logs": [],
            }
            self.state["jobs"][job_id] = job
            self._save_locked()
            return deepcopy(job)

    def get_job(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.state["jobs"].get(job_id)
            return deepcopy(job) if job else None

    def append_log(self, job_id: str, line: str):
        with self.lock:
            job = self.state["jobs"].get(job_id)
            if not job:
                return
            logs = job.setdefault("logs", [])
            if line:
                logs.append(f"[{now_hms()}] {line}")
            else:
                logs.append("")
            if len(logs) > MAX_LOG_LINES:
                del logs[: len(logs) - MAX_LOG_LINES]
            job["updated_at"] = now_iso()
            self._save_locked()

    def update_job(self, job_id: str, **changes) -> dict | None:
        with self.lock:
            job = self.state["jobs"].get(job_id)
            if not job:
                return None
            job.update(changes)
            job["updated_at"] = now_iso()
            self._save_locked()
            return deepcopy(job)

    def request_stop(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.state["jobs"].get(job_id)
            if not job:
                return None
            if job.get("target") != "smartedu_lmc":
                return deepcopy(job)
            if job.get("status") in {"queued", "running"}:
                job["status"] = "pause_requested"
                job["updated_at"] = now_iso()
                job.setdefault("logs", []).append(f"[{now_hms()}] [JOB] 已收到停止请求，当前任务到达安全点后停止。")
                self._save_locked()
            return deepcopy(job)

    def request_pause(self, job_id: str) -> dict | None:
        return self.request_stop(job_id)

    def find_latest_by_course(self, client_id: str, course_key: str) -> dict | None:
        with self.lock:
            matches = [
                job
                for job in self.state["jobs"].values()
                if job.get("client_id") == client_id and job.get("course_key") == course_key
            ]
            matches.sort(key=lambda job: job.get("updated_at") or job.get("created_at") or "", reverse=True)
            return deepcopy(matches[0]) if matches else None

    def find_active_by_course_token(self, course_key: str, token_hash: str) -> dict | None:
        with self.lock:
            matches = [
                job
                for job in self.state["jobs"].values()
                if job.get("course_key") == course_key
                and job.get("token_hash") == token_hash
                and job.get("status") in ACTIVE_STATUSES
            ]
            matches.sort(key=lambda job: job.get("updated_at") or job.get("created_at") or "", reverse=True)
            return deepcopy(matches[0]) if matches else None

    def find_active_by_course(self, client_id: str, course_key: str) -> dict | None:
        with self.lock:
            matches = [
                job
                for job in self.state["jobs"].values()
                if job.get("client_id") == client_id
                and job.get("course_key") == course_key
                and job.get("status") in ACTIVE_STATUSES
            ]
            matches.sort(key=lambda job: job.get("updated_at") or job.get("created_at") or "", reverse=True)
            return deepcopy(matches[0]) if matches else None

    def mark_unfinished_interrupted(self):
        with self.lock:
            changed = False
            for job in self.state["jobs"].values():
                if job.get("status") in ACTIVE_STATUSES:
                    job["status"] = "interrupted"
                    job["ok"] = False
                    job["error"] = "Server restarted before this job completed."
                    job["finished_at"] = now_iso()
                    job["updated_at"] = now_iso()
                    job.setdefault("logs", []).append(f"[{now_hms()}] [JOB] 服务器重启，之前未完成的任务已标记为中断。")
                    changed = True
            if changed:
                self._save_locked()
