from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse


DEFAULT_CONCURRENCY = 5
DEFAULT_SLEEP = 0.2
DEFAULT_BASE_URL = "https://www.icourses.cn/higher_smartedu/course"
DEFAULT_AES_KEY = "b7iPrEmYC8AWPGsAH6VBiA=="
DETAIL_ENDPOINT_HIERARCHICAL = "/smartHigerEdu/hierarchicalCourseDetail"
DETAIL_ENDPOINT_LECTURE = "/smartHigerEdu/lectureCourseDetail"

VIDEO_TYPE_VALUES = {"1", "video", "VIDEO", "mp4", "MP4", "vod", "VOD"}
RESOURCE_LIST_KEYS = (
    "resList",
    "resourceList",
    "resources",
    "videoList",
    "reses",
    "courseResList",
    "courseResourceList",
    "resourceInfoList",
    "resInfoList",
    "lessonList",
    "coursewareList",
    "items",
    "records",
    "list",
)
CHILDREN_KEYS = (
    "chapterResTree",
    "children",
    "childList",
    "chapterList",
    "chapters",
    "nodes",
    "subList",
    "catalogList",
    "directoryList",
    "unitList",
)
RESOURCE_ID_KEYS = (
    "id",
    "resId",
    "resID",
    "resourceId",
    "resourceID",
    "res_id",
    "resource_id",
    "videoId",
    "videoID",
    "video_id",
    "videoResourceId",
)
RESOURCE_EXPLICIT_TITLE_KEYS = (
    "resTitle",
    "resourceTitle",
    "title",
    "resourceName",
    "resName",
    "videoTitle",
    "videoName",
    "fileName",
    "displayName",
)
RESOURCE_TITLE_KEYS = RESOURCE_EXPLICIT_TITLE_KEYS + ("name",)
RESOURCE_PROGRESS_KEYS = ("progress", "studyProgress", "learningProgress")
RESOURCE_SECONDS_KEYS = (
    "videoClassHour",
    "video_seconds",
    "duration",
    "videoDuration",
    "durationSecond",
    "durationSeconds",
    "seconds",
    "videoSeconds",
    "classHour",
    "resDuration",
)
RESOURCE_MEDIA_KEYS = ("videoUrl", "videoURL", "playUrl", "playURL", "mediaUrl", "mediaURL", "fileUrl", "fileURL")


class MissingDependencyError(RuntimeError):
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


def normalize_base_url(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Please provide an API base URL or course link.")
    if "://" not in raw:
        raw = "https://" + raw

    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("Invalid URL. Example: https://www.example.com/api/course")

    if parsed.netloc.lower() == "service.icourses.cn" and (parsed.path or "").startswith("/resCourse/"):
        return DEFAULT_BASE_URL

    path = (parsed.path or "").rstrip("/")
    marker = "/api/course"
    marker_index = path.find(marker)
    if marker_index >= 0:
        path = path[: marker_index + len(marker)]
    elif not path:
        path = marker

    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", "")).rstrip("/")


def detail_endpoint_from_link(link: str) -> tuple[str, str]:
    path = (urlparse(link or "").path or "").lower()
    if path.endswith("/lecture") or "/lecture/" in path:
        return DETAIL_ENDPOINT_LECTURE, "lecture"
    return DETAIL_ENDPOINT_HIERARCHICAL, "multi-level"


def _origin(base_url: str) -> str:
    parsed = urlparse(base_url)
    return urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")


def aes_encrypt(plaintext: str, aes_key: str) -> str:
    key_bytes = (aes_key or "").encode("utf-8")
    if len(key_bytes) not in (16, 24, 32):
        raise ValueError("AES key must be 16, 24, or 32 bytes when encoded as UTF-8.")

    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad
    except ImportError as exc:
        raise MissingDependencyError(
            "Missing dependency: pycryptodome. Run `python -m pip install -r requirements.txt`."
        ) from exc

    cipher = AES.new(key_bytes, AES.MODE_ECB)
    padded = pad(plaintext.encode("utf-8"), AES.block_size)
    return cipher.encrypt(padded).hex()


def build_session(token: str, base_url: str):
    if not (token or "").strip():
        raise ValueError("Please provide a token.")

    requests = _load_requests()
    origin = _origin(base_url)
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": token.strip(),
            "Accept": "application/json, text/plain, */*",
            "Origin": origin,
            "Referer": f"{origin}/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
        }
    )
    return session


def api_get_json(session, url: str, params=None):
    resp = session.get(url, params=params, timeout=20)
    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"[NON_JSON_RESPONSE] {resp.status_code}: {resp.text[:300]}")
    if resp.status_code != 200:
        raise RuntimeError(f"[HTTP_ERROR] {resp.status_code}: {data}")
    if data.get("code") != 0:
        raise RuntimeError(f"[API_ERROR] {data}")
    return data.get("data", {})


def _first_value(mapping: dict[str, Any], keys: tuple[str, ...]):
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _resource_type(resource: dict[str, Any]):
    return _first_value(resource, ("type", "resType", "resourceType", "mediaType"))


def _has_non_empty_key(mapping: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return _first_value(mapping, keys) is not None


def _has_nested_collection(mapping: dict[str, Any]) -> bool:
    for key in RESOURCE_LIST_KEYS + CHILDREN_KEYS:
        value = mapping.get(key)
        if isinstance(value, list) and value:
            return True
    return False


def _looks_like_resource_context(parent_key: str | None) -> bool:
    if not parent_key:
        return False
    key = parent_key.lower()
    return any(marker in key for marker in ("res", "resource", "video", "media", "lesson", "courseware"))


def _is_video_resource(resource: dict[str, Any], parent_key: str | None = None) -> bool:
    resource_id = _first_value(resource, RESOURCE_ID_KEYS)
    if resource_id is None:
        return False

    resource_type = _resource_type(resource)
    if resource_type is not None:
        resource_type_text = str(resource_type).strip()
        return resource_type_text in VIDEO_TYPE_VALUES or resource_type_text.lower() == "video"

    if _has_non_empty_key(resource, RESOURCE_SECONDS_KEYS) or _has_non_empty_key(resource, RESOURCE_MEDIA_KEYS):
        return True

    if _has_non_empty_key(resource, RESOURCE_EXPLICIT_TITLE_KEYS):
        return _looks_like_resource_context(parent_key) or not _has_nested_collection(resource)

    # Some no-chapter course responses use video nodes with only id + name.
    # Do not apply this fallback to nodes that still look like chapter containers.
    if resource.get("name") and _looks_like_resource_context(parent_key) and not _has_nested_collection(resource):
        return True

    return False


def extract_videos_from_course_data(course_data: dict[str, Any]) -> list[dict[str, Any]]:
    videos: list[dict[str, Any]] = []
    seen_resource_ids: set[str] = set()

    def add_video(resource: dict[str, Any], path: list[str]):
        resource_id = _first_value(resource, RESOURCE_ID_KEYS)
        if resource_id is None:
            return

        resource_key = str(resource_id)
        if resource_key in seen_resource_ids:
            return
        seen_resource_ids.add(resource_key)

        videos.append(
            {
                "res_id": resource_id,
                "title": _first_value(resource, RESOURCE_TITLE_KEYS) or "",
                "chapter": " / ".join(path),
                "progress": _first_value(resource, RESOURCE_PROGRESS_KEYS),
                "video_seconds": _first_value(resource, RESOURCE_SECONDS_KEYS),
            }
        )

    def walk(value, path: list[str], parent_key: str | None = None):
        if isinstance(value, list):
            for item in value:
                walk(item, path, parent_key)
            return

        if not isinstance(value, dict):
            return

        is_video = _is_video_resource(value, parent_key)
        if is_video:
            add_video(value, path)

        node_name = value.get("name") or value.get("chapterName") or ""
        current_path = path
        if node_name and not is_video:
            current_path = path + [node_name]

        for key in RESOURCE_LIST_KEYS:
            resources = value.get(key)
            if isinstance(resources, list):
                for resource in resources:
                    if isinstance(resource, dict) and _is_video_resource(resource, key):
                        add_video(resource, current_path)

        for key in CHILDREN_KEYS:
            children = value.get(key)
            if children:
                walk(children, current_path, key)

        for key, child_value in value.items():
            if key in RESOURCE_LIST_KEYS or key in CHILDREN_KEYS:
                continue
            if isinstance(child_value, (dict, list)):
                walk(child_value, current_path, key)

    walk(course_data, [])
    return videos


def collect_videos(session, base_url: str, detail_endpoint: str = DETAIL_ENDPOINT_HIERARCHICAL):
    detail_url = f"{base_url}{detail_endpoint}"
    course_data = api_get_json(session, detail_url)

    course_name = course_data.get("courseName", "")
    course_school = course_data.get("courseSchool", "")
    videos = extract_videos_from_course_data(course_data)

    for video in videos:
        try:
            detail = api_get_json(
                session,
                f"{base_url}/smartHigerEdu/playRes",
                params={"resId": video["res_id"]},
            )
            video["video_seconds"] = detail.get("videoClassHour") or video.get("video_seconds")
            video["title"] = detail.get("resTitle") or video["title"]
        except Exception as exc:
            if video.get("video_seconds") is None:
                video["video_seconds"] = None
            video["error"] = str(exc)

    return course_name, course_school, videos


def send_ticker(session, base_url: str, aes_key: str, res_id: int, total_seconds: float):
    played_range = f"0.00-{total_seconds:.2f}"
    encrypted = aes_encrypt(played_range, aes_key)
    payload = {
        "resId": res_id,
        "tickerTime": int(time.time() * 1000),
        "tickerVideoTimeArrayString": encrypted,
    }
    resp = session.post(f"{base_url}/selfticker/tickerVideo", json=payload, timeout=10)
    try:
        return resp.json()
    except Exception:
        return resp.text if resp.ok else f"{resp.status_code}: {resp.text[:300]}"


def run_job(
    token: str,
    link: str,
    aes_key: str,
    concurrency: int = DEFAULT_CONCURRENCY,
    sleep_seconds: float = DEFAULT_SLEEP,
    logger: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    logs: list[str] = []

    def log(message: str):
        logs.append(message)
        if logger:
            logger(message)

    started_at = time.time()
    try:
        base_url = normalize_base_url(link)
        detail_endpoint, course_page_type = detail_endpoint_from_link(link)
        concurrency = max(1, int(concurrency or DEFAULT_CONCURRENCY))
        sleep_seconds = max(0.0, float(sleep_seconds or 0))

        log(f"[BASE_URL] {base_url}")
        log(f"[COURSE_PAGE] {course_page_type}")
        log("[FETCHING_VIDEO_LIST]")
        session = build_session(token, base_url)
        course_name, course_school, videos = collect_videos(session, base_url, detail_endpoint)
        log(f"[COURSE] {course_name} ({course_school})")
        log(f"[TOTAL_VIDEOS] {len(videos)}")

        valid_videos = [video for video in videos if video.get("video_seconds") is not None]
        if not valid_videos:
            log("[NO_VALID_VIDEOS]")
            return {
                "ok": True,
                "base_url": base_url,
                "course_name": course_name,
                "course_school": course_school,
                "video_count": len(videos),
                "valid_count": 0,
                "success_count": 0,
                "fail_count": 0,
                "total_seconds": 0,
                "total_hms": "00:00",
                "elapsed_seconds": round(time.time() - started_at, 2),
                "logs": logs,
            }

        log(f"[VALID_VIDEOS] {len(valid_videos)}, [CONCURRENCY] {concurrency}")
        success_count = 0
        fail_count = 0

        def report_one(video):
            if sleep_seconds:
                time.sleep(sleep_seconds)
            local_session = build_session(token, base_url)
            rid = video["res_id"]
            seconds = float(video["video_seconds"])
            try:
                result = send_ticker(local_session, base_url, aes_key, rid, seconds)
                if isinstance(result, dict) and result.get("code") == 0:
                    return True, rid, video["title"], seconds, result
                return False, rid, video["title"], seconds, result
            except Exception as exc:
                return False, rid, video["title"], seconds, str(exc)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_map = {executor.submit(report_one, video): video for video in valid_videos}
            for future in as_completed(future_map):
                ok, rid, title, seconds, message = future.result()
                duration = hms(seconds)
                if ok:
                    success_count += 1
                    log(f"[OK] id={rid} title={title} duration={duration}")
                else:
                    fail_count += 1
                    log(f"[FAIL] id={rid} title={title} duration={duration} reason={message}")

        total_sec = sum(float(video["video_seconds"]) for video in valid_videos)
        log(f"[COMPLETE] success={success_count}, fail={fail_count}")
        log(f"[TOTAL_DURATION] {hms(total_sec)} ({int(round(total_sec))} seconds)")

        return {
            "ok": True,
            "base_url": base_url,
            "course_name": course_name,
            "course_school": course_school,
            "video_count": len(videos),
            "valid_count": len(valid_videos),
            "success_count": success_count,
            "fail_count": fail_count,
            "total_seconds": int(round(total_sec)),
            "total_hms": hms(total_sec),
            "elapsed_seconds": round(time.time() - started_at, 2),
            "logs": logs,
        }
    except Exception as exc:
        log(f"[ERROR] {exc}")
        return {
            "ok": False,
            "error": str(exc),
            "elapsed_seconds": round(time.time() - started_at, 2),
            "logs": logs,
        }


def main():
    parser = argparse.ArgumentParser(description="Run the request tester from the command line.")
    parser.add_argument("--token", required=True, help="Authorization token.")
    parser.add_argument("--link", default=DEFAULT_BASE_URL, help="API base URL or course link.")
    parser.add_argument("--aes-key", default=DEFAULT_AES_KEY, help="AES key, 16/24/32 UTF-8 bytes.")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="Worker count.")
    parser.add_argument("--sleep", type=float, default=DEFAULT_SLEEP, help="Delay before each ticker request.")
    args = parser.parse_args()

    result = run_job(
        token=args.token,
        link=args.link,
        aes_key=args.aes_key,
        concurrency=args.concurrency,
        sleep_seconds=args.sleep,
    )
    for line in result.get("logs", []):
        print(line)
    if not result.get("ok"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
