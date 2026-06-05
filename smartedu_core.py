from __future__ import annotations

import argparse
import json
import random
import re
import string
import time
from typing import Any, Callable
from urllib.parse import urlparse


API_BASE = "https://api.chinaooc.com.cn/api/v2"
PAGE_API_BASE = "https://api.chinaooc.com.cn/api/v1"
DEFAULT_PAGE_BASE = "https://higher.smartedu.cn"
DEFAULT_COURSE_TYPE = "lmc"

INITIAL_DELAY_1 = 15.0
INITIAL_SEGMENT_1 = 15
SECOND_DELAY = 45.0
SECOND_SEGMENT = 45
SUBSEQUENT_INTERVAL = 60.0
SUBSEQUENT_SEGMENT = 60


class MissingDependencyError(RuntimeError):
    pass


class PauseRequested(RuntimeError):
    pass


def _load_requests():
    try:
        import requests
    except ImportError as exc:
        raise MissingDependencyError(
            "Missing dependency: requests. Run `python -m pip install -r requirements.txt`."
        ) from exc
    return requests


def hms(seconds):
    try:
        seconds = int(round(float(seconds)))
    except Exception:
        return ""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def random_sid(length=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def short_id(value: str | None) -> str:
    text = value or ""
    if len(text) <= 8:
        return text or "-"
    return text[:8] + "..."


def interruptible_sleep(seconds: float, should_pause: Callable[[], bool] | None = None):
    remaining = max(0.0, float(seconds or 0))
    while remaining > 0:
        if should_pause and should_pause():
            raise PauseRequested("Pause requested.")
        chunk = min(1.0, remaining)
        time.sleep(chunk)
        remaining -= chunk
    if should_pause and should_pause():
        raise PauseRequested("Pause requested.")


def build_headers(token: str) -> dict:
    token = token.strip()
    if not token.lower().startswith("bearer "):
        token = "Bearer " + token
    return {
        "Authorization": token,
        "Accept": "application/json, text/plain, */*",
        "Origin": DEFAULT_PAGE_BASE,
        "Referer": f"{DEFAULT_PAGE_BASE}/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json",
    }


def build_session(token: str):
    requests = _load_requests()
    session = requests.Session()
    session.headers.update(build_headers(token))
    return session


def api_get_json(session, url, params=None, timeout=15):
    resp = session.get(url, params=params, timeout=timeout)
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"[NON_JSON_RESPONSE] HTTP {resp.status_code}: {resp.text[:300]}")
    if resp.status_code >= 400:
        raise RuntimeError(f"[HTTP_ERROR] HTTP {resp.status_code}: {data}")
    return data


def api_post_json(session, url, body, timeout=10):
    resp = session.post(url, json=body, timeout=timeout)
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"[NON_JSON_RESPONSE] HTTP {resp.status_code}: {resp.text[:300]}")
    if resp.status_code >= 400:
        raise RuntimeError(f"[HTTP_ERROR] HTTP {resp.status_code}: {data}")
    return data


def build_course_url(course_id: str, course_type: str = DEFAULT_COURSE_TYPE) -> str:
    return f"{DEFAULT_PAGE_BASE}/course/{course_type}/{course_id}"


def parse_course_from_url(url: str) -> tuple[str, str]:
    parsed = urlparse((url or "").strip())
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 3 and path_parts[0] == "course":
        return path_parts[1], path_parts[2]
    raise ValueError("Unable to parse course type and id from /course/{type}/{id}.")


def extract_term_from_page_api(session, log: Callable[[str], None]):
    log("[DEBUG] Try term from /api/v1/page/ai2026")
    try:
        resp = session.get(f"{PAGE_API_BASE}/page/ai2026", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            term = data.get("trainingName") or ""
            if term:
                log(f"[DEBUG] Term from page API: {term}")
                return term
    except Exception as exc:
        log(f"[DEBUG] Page API term request failed: {exc}")
    return None


def extract_term_from_html(session, course_url, log: Callable[[str], None]):
    log("[DEBUG] Try term from course HTML")
    resp = session.get(course_url, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"Course page request failed, HTTP {resp.status_code}")

    match = re.search(
        r'<script\s+type="application/json"\s+id="__NUXT_DATA__"[^>]*>(.*?)</script>',
        resp.text,
        flags=re.DOTALL,
    )
    if not match:
        raise RuntimeError("__NUXT_DATA__ not found in course page")

    try:
        nuxt_data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Failed to parse __NUXT_DATA__ JSON") from exc

    def find_term(obj):
        if isinstance(obj, dict):
            if "term" in obj:
                return obj["term"]
            for value in obj.values():
                found = find_term(value)
                if found:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = find_term(item)
                if found:
                    return found
        return None

    term = find_term(nuxt_data)
    if term:
        log(f"[DEBUG] Term from HTML: {term}")
        return term
    raise RuntimeError("term field not found in __NUXT_DATA__")


def get_term(session, course_url, log: Callable[[str], None]):
    term = extract_term_from_page_api(session, log)
    if term:
        return term
    return extract_term_from_html(session, course_url, log)


def get_course_info(session, course_id: str, course_url: str, log: Callable[[str], None]):
    started_at = time.time()
    data = api_get_json(session, f"{API_BASE}/CourseLMC/{course_id}")
    course_name = data.get("courseName", "")
    videos_raw = data.get("videos") or []

    term = data.get("term") or ""
    if not term:
        term = get_term(session, course_url, log)

    videos = [
        {
            "title": video.get("title") or "",
            "videoUrl": video.get("videoUrl") or "",
            "videoDuration": float(video.get("videoDuration") or 0),
        }
        for video in videos_raw
    ]
    log(f"[DEBUG] Course info fetched in {time.time() - started_at:.2f}s")
    return term, course_name, videos


def get_existing_record(session, course_id, course_type, term, video_url, log: Callable[[str], None]):
    params = {
        "courseId": course_id,
        "courseType": course_type,
        "term": term,
        "videoUrl": video_url,
    }
    try:
        data = api_get_json(session, f"{API_BASE}/CourseLearningRecords/getOneByVideo", params=params)
        if data and isinstance(data, dict):
            records = data.get("records") or []
            if records:
                first = records[0]
                return (
                    first.get("_id"),
                    first.get("recordId"),
                    float(first.get("currTime") or 0),
                )
    except Exception as exc:
        log(f"[WARN] Existing record query failed: {exc}")
    return None, None, 0.0


def is_video_complete(learned_seconds: float, duration_seconds: float) -> bool:
    if duration_seconds <= 0:
        return True
    return float(learned_seconds or 0) >= max(0.0, float(duration_seconds) - 1.0)


def collect_video_statuses(session, course_id, course_type, term, videos, log: Callable[[str], None]):
    statuses = []
    total_duration = 0.0
    total_learned = 0.0
    completed_count = 0

    log("[INFO] 正在从 SmartEdu 服务器查询观看记录...")
    for index, video in enumerate(videos, start=1):
        title = video.get("title") or "(untitled)"
        video_url = video.get("videoUrl") or ""
        duration = float(video.get("videoDuration") or 0)
        _, _, learned = get_existing_record(session, course_id, course_type, term, video_url, log)
        learned = min(float(learned or 0), duration) if duration > 0 else float(learned or 0)
        complete = is_video_complete(learned, duration)

        total_duration += duration
        total_learned += learned
        if complete:
            completed_count += 1

        status = {
            "index": index,
            "title": title,
            "video_url": video_url,
            "duration_seconds": duration,
            "learned_seconds": learned,
            "duration_hms": hms(duration),
            "learned_hms": hms(learned),
            "progress_percent": round(learned / duration * 100, 2) if duration else 100,
            "complete": complete,
        }
        statuses.append(status)

        state = "已完成" if complete else "未完成"
        log(
            f"[WATCH_STATUS] {index}/{len(videos)} {state} | {title} | "
            f"已学 {int(learned)}s({hms(learned)}) / 总长 {int(duration)}s({hms(duration)})"
        )

    summary = {
        "video_count": len(videos),
        "completed_count": completed_count,
        "unfinished_count": max(0, len(videos) - completed_count),
        "total_duration_seconds": total_duration,
        "total_learned_seconds": total_learned,
        "progress_percent": round(total_learned / total_duration * 100, 2) if total_duration else 100,
    }
    return statuses, summary


def start_record(session, course_id, course_type, term, video_url, sid):
    body = {
        "courseId": course_id,
        "courseType": course_type,
        "term": term,
        "videoUrl": video_url,
        "sid": sid,
    }
    data = api_post_json(session, f"{API_BASE}/CourseLearningRecords/start", body)
    if data.get("_status") != "OK" and not data.get("recordId"):
        raise RuntimeError(f"start failed: {data}")
    return data.get("_id"), data.get("recordId")


def send_heartbeat(session, record_id, main_id, sid, start_sec, end_sec, term):
    body = {
        "id": main_id,
        "recordId": record_id,
        "sid": sid,
        "start": start_sec,
        "end": end_sec,
        "term": term,
    }
    data = api_post_json(session, f"{API_BASE}/CourseLearningRecords/heartbeat", body)
    if data.get("_status") != "OK":
        raise RuntimeError(f"heartbeat failed: {data}")
    return data


def report_video_full(
    session,
    video_info,
    course_id,
    course_type,
    term,
    log: Callable[[str], None],
    should_pause: Callable[[], bool] | None = None,
):
    title = video_info["title"] or "(untitled)"
    video_url = video_info["videoUrl"]
    total_sec = float(video_info["videoDuration"] or 0)
    if should_pause and should_pause():
        raise PauseRequested("Pause requested.")

    if total_sec <= 0:
        log(f"[{title}] Duration is 0, skipped")
        return True, title, total_sec, 0.0, "duration is 0"

    sid = random_sid()
    started_at = time.time()
    main_id, record_id, curr_time = get_existing_record(session, course_id, course_type, term, video_url, log)
    current = float(curr_time or 0)

    if is_video_complete(current, total_sec):
        log(f"[{title}] Complete record found, skipped")
        return True, title, total_sec, 0.0, "existing complete record"

    if not main_id or not record_id:
        try:
            main_id, record_id = start_record(session, course_id, course_type, term, video_url, sid)
            log(f"[{title}] New record created (id={short_id(main_id)}, recordId={short_id(record_id)})")
            current = max(current, 0.0)
        except Exception as exc:
            log(f"[{title}] start failed: {exc}")
            return False, title, total_sec, time.time() - started_at, f"start failed: {exc}"
    else:
        log(f"[{title}] Reuse record (progress {curr_time}/{total_sec}s)")

    segment_count = 0

    schedule = [
        (INITIAL_DELAY_1, INITIAL_SEGMENT_1),
        (SECOND_DELAY, SECOND_SEGMENT),
    ]

    try:
        for delay, segment in schedule:
            if current >= total_sec:
                break
            interruptible_sleep(delay, should_pause)
            end = min(current + segment, total_sec)
            send_heartbeat(session, record_id, main_id, sid, current, end, term)
            segment_count += 1
            log(f"[{title}] heartbeat #{segment_count}: {current}-{end}s ({end}/{total_sec}s)")
            current = end

        while current < total_sec:
            interruptible_sleep(SUBSEQUENT_INTERVAL, should_pause)
            end = min(current + SUBSEQUENT_SEGMENT, total_sec)
            send_heartbeat(session, record_id, main_id, sid, current, end, term)
            segment_count += 1
            log(f"[{title}] heartbeat #{segment_count}: {current}-{end}s ({end}/{total_sec}s)")
            current = end
    except PauseRequested:
        log(f"[{title}] Paused at {current}/{total_sec}s")
        raise
    except Exception as exc:
        log(f"[{title}] heartbeat failed after {segment_count} segment(s): {exc}")
        return (
            False,
            title,
            total_sec,
            time.time() - started_at,
            f"heartbeat failed after {segment_count} segment(s): {exc}",
        )

    elapsed = time.time() - started_at
    log(f"[{title}] Report complete ({segment_count} segment(s)), elapsed {elapsed:.1f}s")
    return True, title, total_sec, elapsed, f"success ({segment_count} segment(s))"


def run_job(
    token: str,
    course_id: str,
    course_type: str = DEFAULT_COURSE_TYPE,
    course_url: str | None = None,
    term: str | None = None,
    logger: Callable[[str], None] | None = None,
    should_pause: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    logs: list[str] = []

    def log(message: str):
        logs.append(message)
        if logger:
            logger(message)

    started_at = time.time()
    try:
        if not token.strip():
            raise ValueError("Token is required.")
        if not course_id.strip():
            raise ValueError("Course id is required.")

        course_type = (course_type or DEFAULT_COURSE_TYPE).strip()
        course_url = course_url or build_course_url(course_id, course_type)

        session = build_session(token)
        log(f"[INFO] Course ID: {course_id}, type: {course_type}")
        if should_pause and should_pause():
            log("[PAUSED] 已暂停。再次输入同一 token-课程链接并点击开始，会重新查询服务器观看状态后继续未完成部分。")
            return {
                "ok": False,
                "paused": True,
                "base_url": DEFAULT_PAGE_BASE,
                "course_id": course_id,
                "course_type": course_type,
                "success_count": 0,
                "fail_count": 0,
                "elapsed_seconds": round(time.time() - started_at, 2),
                "logs": logs,
            }

        resolved_term, course_name, videos = get_course_info(session, course_id, course_url, log)
        if term:
            resolved_term = term

        total_duration = sum(float(video["videoDuration"]) for video in videos)
        log(f"[INFO] Course name: {course_name}, term: {resolved_term}")
        log(f"[INFO] Video count: {len(videos)}")
        log(f"[INFO] Total duration: {hms(total_duration)} ({int(total_duration)} seconds)")

        if not videos:
            log("[INFO] No videos, exit")
            return {
                "ok": True,
                "base_url": DEFAULT_PAGE_BASE,
                "course_id": course_id,
                "course_type": course_type,
                "course_name": course_name,
                "success_count": 0,
                "fail_count": 0,
                "elapsed_seconds": round(time.time() - started_at, 2),
                "logs": logs,
            }

        video_statuses, watch_summary = collect_video_statuses(
            session,
            course_id,
            course_type,
            resolved_term,
            videos,
            log,
        )
        unfinished_videos = [
            video
            for video, status in zip(videos, video_statuses)
            if not status.get("complete")
        ]
        log(
            "[WATCH_SUMMARY] "
            f"completed={watch_summary['completed_count']}, "
            f"unfinished={watch_summary['unfinished_count']}, "
            f"progress={watch_summary['progress_percent']}%"
        )

        if not unfinished_videos:
            log("全部课程已观看完毕！")
            return {
                "ok": True,
                "all_watched": True,
                "base_url": DEFAULT_PAGE_BASE,
                "course_id": course_id,
                "course_type": course_type,
                "course_name": course_name,
                "term": resolved_term,
                "video_count": len(videos),
                "success_count": 0,
                "fail_count": 0,
                "watch_summary": watch_summary,
                "watch_statuses": video_statuses,
                "elapsed_seconds": round(time.time() - started_at, 2),
                "logs": logs,
            }

        log(f"[INFO] 只执行未完成视频：{len(unfinished_videos)}/{len(videos)}")
        log(
            "[INFO] Report rhythm: "
            f"{INITIAL_SEGMENT_1}s->{SECOND_SEGMENT}s->{SUBSEQUENT_SEGMENT}s; "
            f"intervals {INITIAL_DELAY_1}s->{SECOND_DELAY}s->{SUBSEQUENT_INTERVAL}s; serial"
        )

        success_count = 0
        fail_count = 0
        results = []

        for video in unfinished_videos:
            try:
                if should_pause and should_pause():
                    raise PauseRequested("Pause requested.")
                ok, title, duration, elapsed, message = report_video_full(
                    session,
                    video,
                    course_id,
                    course_type,
                    resolved_term,
                    log,
                    should_pause=should_pause,
                )
            except PauseRequested:
                log("[PAUSED] 已暂停。再次输入同一 token-课程链接并点击开始，会重新查询服务器观看状态后继续未完成部分。")
                return {
                    "ok": False,
                    "paused": True,
                    "base_url": DEFAULT_PAGE_BASE,
                    "course_id": course_id,
                    "course_type": course_type,
                    "course_name": course_name,
                    "term": resolved_term,
                    "video_count": len(videos),
                    "unfinished_count": len(unfinished_videos),
                    "success_count": success_count,
                    "fail_count": fail_count,
                    "watch_summary": watch_summary,
                    "watch_statuses": video_statuses,
                    "elapsed_seconds": round(time.time() - started_at, 2),
                    "results": results,
                    "logs": logs,
                }
            if ok:
                success_count += 1
            else:
                fail_count += 1
            results.append(
                {
                    "ok": ok,
                    "title": title,
                    "duration": duration,
                    "elapsed_seconds": round(elapsed, 2),
                    "message": message,
                }
            )

        log(f"[COMPLETE] success={success_count}, fail={fail_count}")
        log("[TOTAL_ELAPSED] SmartEdu run finished")
        return {
            "ok": fail_count == 0,
            "base_url": DEFAULT_PAGE_BASE,
            "course_id": course_id,
            "course_type": course_type,
            "course_name": course_name,
            "term": resolved_term,
            "video_count": len(videos),
            "unfinished_count": len(unfinished_videos),
            "success_count": success_count,
            "fail_count": fail_count,
            "watch_summary": watch_summary,
            "watch_statuses": video_statuses,
            "elapsed_seconds": round(time.time() - started_at, 2),
            "results": results,
            "logs": logs,
        }
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return {
            "ok": False,
            "error": str(exc),
            "base_url": DEFAULT_PAGE_BASE,
            "course_id": course_id,
            "course_type": course_type,
            "success_count": 0,
            "fail_count": 1,
            "elapsed_seconds": round(time.time() - started_at, 2),
            "logs": logs,
        }


def main():
    parser = argparse.ArgumentParser(description="Run the SmartEdu request tester from the command line.")
    parser.add_argument("--token", required=True)
    parser.add_argument("--url", help="Course URL, e.g. https://higher.smartedu.cn/course/lmc/<id>.")
    parser.add_argument("--course-id", help="Course id from /course/lmc/<id>.")
    parser.add_argument("--course-type", default=DEFAULT_COURSE_TYPE)
    parser.add_argument("--term", help="Optional term override.")
    args = parser.parse_args()

    course_type = args.course_type
    course_id = args.course_id
    if args.url:
        course_type, course_id = parse_course_from_url(args.url)
    if not course_id:
        raise SystemExit("--course-id or --url is required")

    result = run_job(
        token=args.token,
        course_id=course_id,
        course_type=course_type,
        course_url=args.url,
        term=args.term,
    )
    for line in result.get("logs", []):
        print(line)
    if not result.get("ok"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
