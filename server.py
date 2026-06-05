from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from core import DEFAULT_AES_KEY, DEFAULT_BASE_URL, DEFAULT_CONCURRENCY, DEFAULT_SLEEP, run_job
from job_store import JobStore, now_iso
from smartedu_core import run_job as run_smartedu_job
from token_processor import (
    TARGET_SMARTEDU_LMC,
    TokenEntry,
    build_smartedu_course_key,
    extract_smartedu_course_from_text,
    parse_token_input,
)


ROOT = Path(__file__).resolve().parent
STATIC_ROOT = ROOT / "static"
JOB_STORE = JobStore()
JOB_STORE.mark_unfinished_interrupted()
JOB_THREADS: dict[str, threading.Thread] = {}
JOB_THREADS_LOCK = threading.Lock()
MAX_REQUEST_BYTES = int(os.getenv("REQUEST_TESTER_MAX_REQUEST_BYTES", str(256 * 1024)))


def mask_token(token: str) -> str:
    value = token or ""
    if len(value) <= 10:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


def sanitize_client_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_.:-]", "", (value or "").strip())
    return cleaned[:80] or "anonymous"


def mask_input_text(text: str) -> str:
    lines = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        match = re.search(r"https?://\S+", line, re.IGNORECASE)
        if match and line[: match.start()].strip().endswith("-"):
            prefix = line[: match.start()].strip()
            url = line[match.start() :].strip()
            lines.append(f"{mask_token(prefix[:-1])}-{url}")
            continue

        lines.append(re.sub(r"token=([^&#\s]+)", lambda item: f"token={mask_token(item.group(1))}", line, flags=re.IGNORECASE))
    return "\n".join(lines)


def job_status_label(status: str) -> str:
    return {
        "queued": "排队中",
        "running": "运行中",
        "pause_requested": "暂停中",
        "paused": "已暂停",
        "completed": "已完成",
        "failed": "失败",
        "interrupted": "已中断",
    }.get(status or "", status or "未知")


def course_key_from_entry(entry: TokenEntry) -> str | None:
    if entry.target != TARGET_SMARTEDU_LMC or not entry.course_id:
        return None
    return build_smartedu_course_key(entry.course_type or "lmc", entry.course_id)


def build_run_payload(payload: dict) -> dict:
    token_text = (payload.get("token_or_link") or payload.get("token") or "").strip()
    entries, parse_errors = parse_token_input(token_text)
    base_url = (
        payload.get("base_url")
        or payload.get("link")
        or os.getenv("REQUEST_TESTER_BASE_URL")
        or DEFAULT_BASE_URL
    )

    return {
        "raw_input": token_text,
        "token_entries": entries,
        "parse_errors": parse_errors,
        "base_url": base_url,
        "aes_key": payload.get("aes_key")
        or os.getenv("REQUEST_TESTER_AES_KEY")
        or os.getenv("AES_KEY")
        or DEFAULT_AES_KEY,
        "concurrency": payload.get("concurrency", DEFAULT_CONCURRENCY),
        "sleep": payload.get("sleep", DEFAULT_SLEEP),
    }


def run_token_entries(
    token_entries: list[TokenEntry],
    base_url: str,
    aes_key: str,
    concurrency: int,
    sleep_seconds: float,
    parse_errors: list[str],
    logger=None,
    pause_checker=None,
) -> dict:
    logs: list[str] = []
    started_at = time.time()

    def emit(message: str):
        logs.append(message)
        if logger:
            logger(message)

    invalid_count = len(parse_errors)
    for error in parse_errors:
        emit(f"[INPUT_ERROR] {error}")

    if not token_entries:
        emit("[ERROR] 未找到合法链接。请输入包含 token= 的链接，或 token-https://higher.smartedu.cn/course/lmc/课程ID。")
        return {
            "ok": False,
            "error": "No valid link found.",
            "token_count": 0,
            "success_count": 0,
            "fail_count": max(1, invalid_count),
            "elapsed_seconds": round(time.time() - started_at, 2),
            "logs": logs,
        }

    emit(f"[LINKS] valid={len(token_entries)}, invalid={invalid_count}")
    success_count = 0
    fail_count = invalid_count
    all_ok = invalid_count == 0
    results = []

    for index, entry in enumerate(token_entries, start=1):
        emit("")
        entry_header = (
            f"[LINK {index}/{len(token_entries)}] line={entry.line_number} "
            f"source={entry.source} target={entry.target} token={mask_token(entry.token)}"
        )
        if entry.course_id:
            entry_header += f" course_id={entry.course_id}"
        emit(entry_header)

        if entry.target == TARGET_SMARTEDU_LMC:
            result = run_smartedu_job(
                token=entry.token,
                course_id=entry.course_id or "",
                course_type=entry.course_type or "lmc",
                course_url=entry.url,
                logger=lambda line: emit(f"  {line}"),
                should_pause=pause_checker,
            )
        else:
            result = run_job(
                token=entry.token,
                link=base_url,
                aes_key=aes_key,
                concurrency=concurrency,
                sleep_seconds=sleep_seconds,
                logger=lambda line: emit(f"  {line}"),
            )

        token_success = int(result.get("success_count") or 0)
        if result.get("paused"):
            token_fail = int(result.get("fail_count") or 0)
            all_ok = False
        elif result.get("ok"):
            token_fail = int(result.get("fail_count") or 0)
        else:
            token_fail = int(result.get("fail_count") or 1)
            all_ok = False

        success_count += token_success
        fail_count += token_fail
        results.append(
            {
                "ok": bool(result.get("ok")),
                "line_number": entry.line_number,
                "source": entry.source,
                "target": entry.target,
                "base_url": result.get("base_url") or base_url,
                "course_id": result.get("course_id") or entry.course_id,
                "course_type": result.get("course_type") or entry.course_type,
                "all_watched": bool(result.get("all_watched")),
                "paused": bool(result.get("paused")),
                "unfinished_count": result.get("unfinished_count"),
                "watch_summary": result.get("watch_summary"),
                "success_count": token_success,
                "fail_count": token_fail,
                "error": result.get("error"),
            }
        )

    emit("")
    emit(f"[ALL_COMPLETE] links={len(token_entries)}, invalid={invalid_count}, success={success_count}, fail={fail_count}")
    return {
        "ok": all_ok,
        "token_count": len(token_entries),
        "success_count": success_count,
        "fail_count": fail_count,
        "elapsed_seconds": round(time.time() - started_at, 2),
        "results": results,
        "logs": logs,
    }


def serialize_job(job: dict | None) -> dict | None:
    if not job:
        return None
    return {
        "id": job.get("id"),
        "client_id": job.get("client_id"),
        "target": job.get("target"),
        "course_key": job.get("course_key"),
        "course_url": job.get("course_url"),
        "input_preview": job.get("input_preview"),
        "status": job.get("status"),
        "status_label": job_status_label(job.get("status") or ""),
        "ok": job.get("ok"),
        "all_watched": bool(job.get("all_watched")),
        "paused": bool(job.get("paused")),
        "error": job.get("error"),
        "success_count": int(job.get("success_count") or 0),
        "fail_count": int(job.get("fail_count") or 0),
        "token_count": int(job.get("token_count") or 0),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "elapsed_seconds": job.get("elapsed_seconds") or 0,
        "results": job.get("results") or [],
        "logs": job.get("logs") or [],
    }


def run_background_job(job_id: str, run_payload: dict):
    current_job = JOB_STORE.get_job(job_id)
    if current_job and current_job.get("status") != "pause_requested":
        JOB_STORE.update_job(job_id, status="running", started_at=now_iso())
    elif current_job:
        JOB_STORE.update_job(job_id, started_at=now_iso())
    JOB_STORE.append_log(job_id, "[JOB] 后台任务已启动。")

    try:
        def pause_requested():
            job = JOB_STORE.get_job(job_id)
            return bool(job and job.get("status") == "pause_requested")

        result = run_token_entries(
            token_entries=run_payload["token_entries"],
            base_url=run_payload["base_url"],
            aes_key=run_payload["aes_key"],
            concurrency=run_payload["concurrency"],
            sleep_seconds=run_payload["sleep"],
            parse_errors=run_payload["parse_errors"],
            logger=lambda line: JOB_STORE.append_log(job_id, line),
            pause_checker=pause_requested,
        )
        status = "paused" if result.get("paused") else ("completed" if result.get("ok") else "failed")
        JOB_STORE.update_job(
            job_id,
            status=status,
            ok=bool(result.get("ok")),
            paused=bool(result.get("paused")),
            error=result.get("error"),
            success_count=int(result.get("success_count") or 0),
            fail_count=int(result.get("fail_count") or 0),
            token_count=int(result.get("token_count") or 0),
            all_watched=any(item.get("all_watched") for item in result.get("results") or []),
            elapsed_seconds=result.get("elapsed_seconds") or 0,
            results=result.get("results") or [],
            finished_at=now_iso(),
        )
        JOB_STORE.append_log(job_id, f"[JOB] 后台任务结束，状态：{job_status_label(status)}。")
    except Exception as exc:
        JOB_STORE.append_log(job_id, f"[JOB_ERROR] {exc}")
        JOB_STORE.update_job(
            job_id,
            status="failed",
            ok=False,
            error=str(exc),
            success_count=0,
            fail_count=1,
            finished_at=now_iso(),
        )
    finally:
        with JOB_THREADS_LOCK:
            JOB_THREADS.pop(job_id, None)


def start_background_job(job_id: str, run_payload: dict):
    thread = threading.Thread(target=run_background_job, args=(job_id, run_payload), daemon=True)
    with JOB_THREADS_LOCK:
        JOB_THREADS[job_id] = thread
    thread.start()


def build_job_from_payload(payload: dict) -> tuple[int, dict]:
    client_id = sanitize_client_id(payload.get("client_id") or "")
    run_payload = build_run_payload(payload)
    entries = run_payload["token_entries"]
    raw_input = run_payload["raw_input"]

    if not entries:
        course = extract_smartedu_course_from_text(raw_input)
        if course:
            return 400, {
                "ok": False,
                "mode": "invalid",
                "error": "查询 SmartEdu 服务器观看状态需要 token-课程链接。",
                "success_count": 0,
                "fail_count": 1,
                "logs": [
                    "[INPUT_ERROR] 第三门课程需要输入 token-课程链接，才能向 SmartEdu 服务器查询观看状态。",
                    "[TIP] 请使用：token-https://higher.smartedu.cn/course/lmc/课程ID",
                ],
            }

        errors = run_payload["parse_errors"] or ["请输入合法链接。"]
        return 400, {
            "ok": False,
            "mode": "invalid",
            "error": "No valid link found.",
            "success_count": 0,
            "fail_count": max(1, len(errors)),
            "logs": [f"[INPUT_ERROR] {error}" for error in errors],
        }

    course_key = None
    course_url = None
    entry_token_hash = None
    if len(entries) == 1 and entries[0].target == TARGET_SMARTEDU_LMC:
        course_key = course_key_from_entry(entries[0])
        course_url = entries[0].url
        entry_token_hash = token_hash(entries[0].token)
        active_job = (
            JOB_STORE.find_active_by_course_token(client_id, course_key, entry_token_hash)
            if course_key and entry_token_hash
            else None
        )
        if active_job:
            return 200, {
                "ok": True,
                "mode": "attached",
                "message": "这门课程已有运行中的后台任务，已切换到当前进程。",
                "job": serialize_job(active_job),
            }

    target = entries[0].target if len(entries) == 1 else "batch"
    job = JOB_STORE.create_job(
        client_id=client_id,
        target=target,
        course_key=course_key,
        course_url=course_url,
        token_hash=entry_token_hash,
        input_preview=mask_input_text(raw_input),
    )
    if course_key:
        JOB_STORE.append_log(job["id"], "[JOB] 已创建第三门课程后台任务。浏览器可关闭，之后输入同一课程链接即可查询。")
    else:
        JOB_STORE.append_log(job["id"], "[JOB] 已创建后台任务。")
    start_background_job(job["id"], run_payload)
    return 202, {
        "ok": True,
        "mode": "started",
        "message": "后台任务已启动。",
        "job": serialize_job(JOB_STORE.get_job(job["id"])),
    }


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_json({"ok": True})
            return
        if path.startswith("/api/jobs/"):
            self._send_job(path)
            return
        self._serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/api/jobs/") and path.endswith("/pause"):
            self._pause_job(path)
            return
        if path not in ("/api/run", "/api/run-stream", "/api/jobs"):
            self._send_json({"ok": False, "error": "Not found"}, status=404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_REQUEST_BYTES:
                self._send_json({"ok": False, "error": "Request body too large."}, status=413)
                return
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw or "{}")
        except Exception:
            self._send_json({"ok": False, "error": "Invalid JSON request."}, status=400)
            return

        if path == "/api/jobs":
            status, data = build_job_from_payload(payload)
            self._send_json(data, status=status)
            return

        if path == "/api/run-stream":
            self._stream_run(payload)
            return

        run_payload = build_run_payload(payload)
        token_entries = run_payload["token_entries"]
        if not token_entries:
            result = run_token_entries(
                token_entries=token_entries,
                base_url=run_payload["base_url"],
                aes_key=run_payload["aes_key"],
                concurrency=run_payload["concurrency"],
                sleep_seconds=run_payload["sleep"],
                parse_errors=run_payload["parse_errors"],
            )
            self._send_json(result, status=400)
            return

        result = run_token_entries(
            token_entries=token_entries,
            base_url=run_payload["base_url"],
            aes_key=run_payload["aes_key"],
            concurrency=run_payload["concurrency"],
            sleep_seconds=run_payload["sleep"],
            parse_errors=run_payload["parse_errors"],
        )
        self._send_json(result, status=200 if result.get("ok") else 400)

    def _stream_run(self, payload: dict):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()

        def send_event(event: dict):
            data = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
            self.wfile.write(data)
            self.wfile.flush()

        try:
            run_payload = build_run_payload(payload)

            def stream_log(line: str):
                send_event({"type": "log", "line": line})

            result = run_token_entries(
                token_entries=run_payload["token_entries"],
                base_url=run_payload["base_url"],
                aes_key=run_payload["aes_key"],
                concurrency=run_payload["concurrency"],
                sleep_seconds=run_payload["sleep"],
                parse_errors=run_payload["parse_errors"],
                logger=stream_log,
            )
            summary = {key: value for key, value in result.items() if key != "logs"}
            send_event({"type": "done", "data": summary})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            try:
                send_event(
                    {
                        "type": "done",
                        "data": {
                            "ok": False,
                            "error": str(exc),
                            "success_count": 0,
                            "fail_count": 1,
                        },
                    }
                )
            except (BrokenPipeError, ConnectionResetError):
                return

    def _send_job(self, path: str):
        job_id = path.rsplit("/", 1)[-1].strip()
        if not job_id:
            self._send_json({"ok": False, "error": "Missing job id."}, status=400)
            return
        job = JOB_STORE.get_job(job_id)
        if not job:
            self._send_json({"ok": False, "error": "Job not found."}, status=404)
            return
        self._send_json({"ok": True, "job": serialize_job(job)})

    def _pause_job(self, path: str):
        parts = [part for part in path.split("/") if part]
        if len(parts) != 4 or parts[0] != "api" or parts[1] != "jobs" or parts[3] != "pause":
            self._send_json({"ok": False, "error": "Not found"}, status=404)
            return

        job_id = parts[2].strip()
        job = JOB_STORE.get_job(job_id)
        if not job:
            self._send_json({"ok": False, "error": "Job not found."}, status=404)
            return
        if job.get("target") != TARGET_SMARTEDU_LMC:
            self._send_json({"ok": False, "error": "只有第三门课程任务支持暂停。"}, status=400)
            return
        if job.get("status") not in ("queued", "running", "pause_requested"):
            self._send_json({"ok": False, "error": "当前任务不在运行中，不能暂停。", "job": serialize_job(job)}, status=400)
            return

        paused_job = JOB_STORE.request_pause(job_id)
        self._send_json({"ok": True, "message": "已发送暂停请求。", "job": serialize_job(paused_job)})

    def _serve_static(self, path: str):
        if path in ("", "/"):
            target = STATIC_ROOT / "index.html"
        else:
            relative = path.lstrip("/")
            target = STATIC_ROOT / relative

        try:
            resolved_root = STATIC_ROOT.resolve()
            resolved_target = target.resolve()
        except FileNotFoundError:
            self._send_json({"ok": False, "error": "Not found"}, status=404)
            return

        if resolved_target != resolved_root and resolved_root not in resolved_target.parents:
            self._send_json({"ok": False, "error": "Not found"}, status=404)
            return
        if not resolved_target.is_file():
            self._send_json({"ok": False, "error": "Not found"}, status=404)
            return

        content_type = mimetypes.guess_type(str(resolved_target))[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript",):
            content_type += "; charset=utf-8"

        data = resolved_target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, status: int = 200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")


def main():
    parser = argparse.ArgumentParser(description="Start the request tester Web UI.")
    parser.add_argument("--host", default=os.getenv("REQUEST_TESTER_HOST", "127.0.0.1"), help="Server host.")
    parser.add_argument("--port", type=int, default=int(os.getenv("REQUEST_TESTER_PORT", "8765")), help="Server port.")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Serving Web UI at {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
