from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, unquote, urlparse


URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
SMARTEDU_COURSE_RE = re.compile(r"/course/lmc/([^/?#]+)", re.IGNORECASE)
TARGET_ICOURSES = "icourses"
TARGET_SMARTEDU_LMC = "smartedu_lmc"


@dataclass
class TokenEntry:
    token: str
    line_number: int
    source: str
    target: str = TARGET_ICOURSES
    course_id: str | None = None
    course_type: str | None = None
    url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _clean_token(value: str) -> str:
    return unquote((value or "").strip().strip("\"'"))


def _extract_smartedu_course(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "higher.smartedu.cn":
        return None
    match = re.search(r"/course/([^/?#]+)/([^/?#]+)", parsed.path or "", re.IGNORECASE)
    if not match:
        return None
    return _clean_token(match.group(1)), _clean_token(match.group(2))


def normalize_smartedu_course_url(course_type: str, course_id: str) -> str:
    return f"https://higher.smartedu.cn/course/{_clean_token(course_type)}/{_clean_token(course_id)}"


def build_smartedu_course_key(course_type: str, course_id: str) -> str:
    return f"{TARGET_SMARTEDU_LMC}:{_clean_token(course_type).lower()}:{_clean_token(course_id)}"


def extract_smartedu_course_from_text(text: str) -> dict | None:
    for match in URL_RE.finditer(text or ""):
        url = match.group(0).strip().rstrip("，,。.;；")
        course = _extract_smartedu_course(url)
        if not course:
            continue
        course_type, course_id = course
        return {
            "course_type": course_type,
            "course_id": course_id,
            "course_key": build_smartedu_course_key(course_type, course_id),
            "url": normalize_smartedu_course_url(course_type, course_id),
        }
    return None


def _extract_token_url_combo(text: str, line_number: int) -> TokenEntry | None:
    match = URL_RE.search(text)
    if not match:
        return None

    prefix = text[: match.start()].strip()
    url = text[match.start() :].strip()
    if not prefix.endswith("-"):
        return None

    token = _clean_token(prefix[:-1])
    course = _extract_smartedu_course(url)
    if not token or not course:
        return None
    course_type, course_id = course
    normalized_url = normalize_smartedu_course_url(course_type, course_id)

    return TokenEntry(
        token=token,
        line_number=line_number,
        source="token_url_combo",
        target=TARGET_SMARTEDU_LMC,
        course_id=course_id,
        course_type=course_type,
        url=normalized_url,
    )


def extract_token_from_line(line: str, line_number: int = 1) -> TokenEntry | None:
    text = (line or "").strip()
    if not text:
        return None

    combo_entry = _extract_token_url_combo(text, line_number)
    if combo_entry:
        return combo_entry

    parsed = urlparse(text)
    is_url = bool(parsed.scheme and parsed.netloc)
    token = ""

    if is_url:
        token_values = parse_qs(parsed.query).get("token") or parse_qs(parsed.fragment).get("token")
        if token_values:
            token = token_values[0]

    token = _clean_token(token)
    if not token:
        return None

    return TokenEntry(
        token=token,
        line_number=line_number,
        source="link" if is_url else "text",
        target=TARGET_ICOURSES,
        url=text if is_url else None,
    )


def describe_invalid_line(line: str, line_number: int) -> str:
    text = (line or "").strip()
    if not text:
        return ""

    parsed = urlparse(text)
    is_url = bool(parsed.scheme and parsed.netloc)
    has_url = bool(URL_RE.search(text))
    smartedu_course = has_url and "higher.smartedu.cn" in text.lower()

    if smartedu_course:
        return (
            f"第 {line_number} 行：SmartEdu 输入格式不合法，请使用 "
            "token-https://higher.smartedu.cn/course/lmc/课程ID"
        )
    if is_url:
        return f"第 {line_number} 行：链接中没有找到 token= 参数，请检查链接是否完整"
    return (
        f"第 {line_number} 行：输入不合法，不支持单独输入 token；请填写包含 token= 的链接，"
        "或 token-https://higher.smartedu.cn/course/lmc/课程ID"
    )


def parse_token_input(text: str) -> tuple[list[TokenEntry], list[str]]:
    entries: list[TokenEntry] = []
    errors: list[str] = []

    for index, line in enumerate((text or "").splitlines(), start=1):
        if not line.strip():
            continue
        entry = extract_token_from_line(line, index)
        if entry:
            entries.append(entry)
        else:
            error = describe_invalid_line(line, index)
            if error:
                errors.append(error)

    return entries, errors


def extract_tokens(text: str) -> list[str]:
    entries, _ = parse_token_input(text)
    return [entry.token for entry in entries]


def main():
    parser = argparse.ArgumentParser(description="Extract tokens from supported links.")
    parser.add_argument("values", nargs="*", help="Link values. If omitted, stdin is used.")
    parser.add_argument("--json", action="store_true", help="Output token metadata as JSON.")
    args = parser.parse_args()

    text = "\n".join(args.values) if args.values else sys.stdin.read()
    entries, errors = parse_token_input(text)
    if args.json:
        print(json.dumps({"tokens": [entry.to_dict() for entry in entries], "errors": errors}, ensure_ascii=False, indent=2))
    else:
        for entry in entries:
            print(entry.token)
        for error in errors:
            print(error, file=sys.stderr)

    if errors and not entries:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
