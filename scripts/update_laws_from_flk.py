"""Update local law texts from the National Laws and Regulations Database.

The script keeps the existing file set and filenames in data/laws, resolves each
title against https://flk.npc.gov.cn/, downloads the official DOCX version, and
rewrites the local TXT file with normalized metadata plus extracted text.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import docx
import requests


BASE_URL = "https://flk.npc.gov.cn"
STATUS_LABELS = {
    1: "历史版本",
    2: "已修改",
    3: "现行有效版本",
    4: "尚未生效",
}


@dataclass
class UpdateResult:
    path: str
    requested_title: str
    official_title: str | None
    desired_sxx: int
    matched_sxx: int | None
    bbbs: str | None
    gbrq: str | None
    sxrq: str | None
    flxz: str | None
    zdjg_name: str | None
    action: str
    error: str | None = None
    old_bbbs: str | None = None


def clean_html(value: str | None) -> str:
    if not value:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def parse_local_header(path: Path) -> tuple[str, str | None]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if not first.startswith("# "):
        raise ValueError("missing '# ' title header")

    display_title = first[2:].strip()
    requested_title = re.sub(r"（历史版本）|\(历史版本\)", "", display_title).strip()

    old_bbbs = None
    for line in text.splitlines()[:12]:
        if line.startswith("版本标识:"):
            old_bbbs = line.split(":", 1)[1].strip()
            break
    return requested_title, old_bbbs


def is_historical(path: Path, requested_title: str) -> bool:
    marker = f"{path.stem} {requested_title}"
    return "历史版本" in marker


class FlkClient:
    def __init__(self, timeout: int = 30, pause: float = 0.25) -> None:
        self.timeout = timeout
        self.pause = pause
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
                ),
                "Referer": f"{BASE_URL}/",
                "Origin": BASE_URL,
                "Content-Type": "application/json;charset=UTF-8",
            }
        )

    def _sleep(self) -> None:
        if self.pause:
            time.sleep(self.pause)

    def _json_from_browser_fetch(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        target_id: str | None = None
        try:
            response = requests.post(
                "http://localhost:3456/new",
                data=f"{BASE_URL}/".encode("utf-8"),
                timeout=15,
            )
            response.raise_for_status()
            target_id = response.json()["targetId"]
            time.sleep(1)

            script = (
                "fetch("
                + json.dumps(path)
                + ",{method:'POST',headers:{'Content-Type':'application/json;charset=UTF-8'},"
                + "body:JSON.stringify("
                + json.dumps(payload, ensure_ascii=False)
                + ")}).then(r=>r.text())"
            )
            last_text = ""
            for _ in range(8):
                eval_response = requests.post(
                    f"http://localhost:3456/eval?target={target_id}",
                    data=script.encode("utf-8"),
                    timeout=20,
                )
                eval_response.raise_for_status()
                last_text = (eval_response.json().get("value") or "").strip()
                if last_text.startswith("{"):
                    return json.loads(last_text)
                time.sleep(1)
            raise RuntimeError(f"browser fetch did not return JSON: {last_text[:200]!r}")
        finally:
            if target_id:
                try:
                    requests.get(f"http://localhost:3456/close?target={target_id}", timeout=5)
                except Exception:
                    pass

    def search(self, title: str, sxx: int) -> list[dict[str, Any]]:
        payload = {
            "searchContent": title,
            "searchType": 1,
            "searchRange": 1,
            "sxx": [sxx],
            "gbrq": [],
            "sxrq": [],
            "pageNum": 1,
            "pageSize": 20,
        }
        response = self.session.post(
            f"{BASE_URL}/law-search/search/list",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        self._sleep()
        try:
            data = response.json()
        except requests.JSONDecodeError:
            data = self._json_from_browser_fetch("/law-search/search/list", payload)
        return data.get("rows") or []

    def details(self, bbbs: str) -> dict[str, Any]:
        response = self.session.get(
            f"{BASE_URL}/law-search/search/flfgDetails",
            params={"bbbs": bbbs},
            timeout=self.timeout,
        )
        response.raise_for_status()
        self._sleep()
        data = response.json()
        if data.get("code") != 200 or not data.get("data"):
            raise RuntimeError(f"detail request failed: {data!r}")
        return data["data"]

    def _json_from_browser(self, url: str) -> dict[str, Any]:
        target_id: str | None = None
        try:
            response = requests.post(
                "http://localhost:3456/new",
                data=url.encode("utf-8"),
                timeout=15,
            )
            response.raise_for_status()
            target_id = response.json()["targetId"]

            last_text = ""
            for _ in range(12):
                time.sleep(1)
                eval_response = requests.post(
                    f"http://localhost:3456/eval?target={target_id}",
                    data="document.body.innerText".encode("utf-8"),
                    timeout=15,
                )
                eval_response.raise_for_status()
                last_text = (eval_response.json().get("value") or "").strip()
                if last_text.startswith("{"):
                    return json.loads(last_text)
            raise RuntimeError(f"browser fallback did not return JSON: {last_text[:200]!r}")
        finally:
            if target_id:
                try:
                    requests.get(f"http://localhost:3456/close?target={target_id}", timeout=5)
                except Exception:
                    pass

    def docx_url(self, bbbs: str) -> str:
        params = {"format": "docx", "bbbs": bbbs, "fileId": ""}
        endpoint = f"{BASE_URL}/law-search/download/pc"
        response = self.session.get(endpoint, params=params, timeout=self.timeout)
        response.raise_for_status()
        self._sleep()
        try:
            data = response.json()
        except requests.JSONDecodeError:
            data = self._json_from_browser(f"{endpoint}?{urlencode(params)}")
        if data.get("code") != 200 or not data.get("data", {}).get("url"):
            raise RuntimeError(f"download link request failed: {data!r}")
        return data["data"]["url"]

    def download_docx(self, url: str) -> bytes:
        response = self.session.get(url, timeout=max(self.timeout, 60))
        response.raise_for_status()
        self._sleep()
        return response.content


def select_exact_match(rows: list[dict[str, Any]], title: str, sxx: int) -> dict[str, Any] | None:
    exact = [
        row
        for row in rows
        if clean_html(row.get("title")) == title and int(row.get("sxx", -1)) == sxx
    ]
    if exact:
        return sorted(exact, key=lambda row: (row.get("gbrq") or "", row.get("sxrq") or ""), reverse=True)[0]

    if title == "中华人民共和国宪法":
        constitution_texts = [
            row
            for row in rows
            if int(row.get("sxx", -1)) == sxx
            and row.get("flxz") == "宪法"
            and clean_html(row.get("title")).startswith("中华人民共和国宪法（")
            and "修正文本" in clean_html(row.get("title"))
        ]
        if constitution_texts:
            return sorted(
                constitution_texts,
                key=lambda row: (row.get("gbrq") or "", row.get("sxrq") or ""),
                reverse=True,
            )[0]
    return None


def extract_docx_text(blob: bytes) -> str:
    document = docx.Document(BytesIO(blob))
    paragraphs = [paragraph.text.strip() for paragraph in document.paragraphs]
    lines = [line for line in paragraphs if line]
    return "\n".join(lines).strip()


def render_law_text(
    detail: dict[str, Any],
    body: str,
    display_historical: bool,
    update_date: str,
) -> str:
    title = detail["title"]
    sxx = int(detail["sxx"])
    display_title = f"{title}（历史版本）" if display_historical else title
    status = STATUS_LABELS.get(sxx, f"未知状态{sxx}")
    header = [
        f"# {display_title}",
        f"来源: 国家法律法规数据库 {BASE_URL}/",
        f"官方标题: {title}",
        f"效力标记: {status}（sxx={sxx}）",
        f"公布日期: {detail.get('gbrq') or ''}",
        f"施行日期: {detail.get('sxrq') or ''}",
        f"制定机关: {detail.get('zdjgName') or ''}",
        f"法规类别: {detail.get('flxz') or ''}",
        f"版本标识: {detail.get('bbbs') or ''}",
        f"更新时间: {update_date}",
        "",
        body,
        "",
    ]
    return "\n".join(header)


def update_one(
    client: FlkClient,
    path: Path,
    update_date: str,
    dry_run: bool,
) -> UpdateResult:
    requested_title, old_bbbs = parse_local_header(path)
    desired_sxx = 1 if is_historical(path, requested_title) else 3

    try:
        rows = client.search(requested_title, desired_sxx)
        match = select_exact_match(rows, requested_title, desired_sxx)
        if not match:
            return UpdateResult(
                path=str(path),
                requested_title=requested_title,
                official_title=None,
                desired_sxx=desired_sxx,
                matched_sxx=None,
                bbbs=None,
                gbrq=None,
                sxrq=None,
                flxz=None,
                zdjg_name=None,
                action="skipped",
                error="no exact title/status match",
                old_bbbs=old_bbbs,
            )

        detail = {
            "bbbs": match["bbbs"],
            "title": clean_html(match.get("title")),
            "sxx": int(match["sxx"]),
            "gbrq": match.get("gbrq"),
            "sxrq": match.get("sxrq"),
            "flxz": match.get("flxz"),
            "zdjgName": match.get("zdjgName"),
        }
        official_title = detail["title"]
        docx_link = client.docx_url(detail["bbbs"])
        body = extract_docx_text(client.download_docx(docx_link))
        expected_title = re.split(r"[（(]", requested_title, maxsplit=1)[0]
        if not body or expected_title not in body[:300]:
            raise RuntimeError("downloaded DOCX did not contain expected title near the top")

        rendered = render_law_text(
            detail,
            body,
            display_historical=desired_sxx == 1,
            update_date=update_date,
        )
        action = "dry-run"
        if not dry_run:
            path.write_text(rendered, encoding="utf-8")
            action = "updated" if old_bbbs != detail["bbbs"] else "refreshed"

        return UpdateResult(
            path=str(path),
            requested_title=requested_title,
            official_title=official_title,
            desired_sxx=desired_sxx,
            matched_sxx=int(detail["sxx"]),
            bbbs=detail["bbbs"],
            gbrq=detail.get("gbrq"),
            sxrq=detail.get("sxrq"),
            flxz=detail.get("flxz"),
            zdjg_name=detail.get("zdjgName"),
            action=action,
            old_bbbs=old_bbbs,
        )
    except Exception as exc:  # noqa: BLE001 - report and continue the batch.
        return UpdateResult(
            path=str(path),
            requested_title=requested_title,
            official_title=None,
            desired_sxx=desired_sxx,
            matched_sxx=None,
            bbbs=None,
            gbrq=None,
            sxrq=None,
            flxz=None,
            zdjg_name=None,
            action="failed",
            error=str(exc),
            old_bbbs=old_bbbs,
        )


def write_reports(results: list[UpdateResult], output_dir: Path, update_date: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"latest_update_report_{update_date}.json"
    md_path = output_dir / f"latest_update_report_{update_date}.md"

    json_path.write_text(
        json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total = len(results)
    failed = [result for result in results if result.action in {"failed", "skipped"}]
    updated = [result for result in results if result.action == "updated"]
    refreshed = [result for result in results if result.action == "refreshed"]
    dry_run = [result for result in results if result.action == "dry-run"]

    lines = [
        f"# 法律语料最新版校验报告（{update_date}）",
        "",
        f"- 数据源: 国家法律法规数据库 {BASE_URL}/",
        f"- 本地目录: {output_dir}",
        f"- 处理文件: {total}",
        f"- 新版本/版本标识变化: {len(updated)}",
        f"- 已刷新但版本标识未变: {len(refreshed)}",
        f"- 只读校验: {len(dry_run)}",
        f"- 失败/跳过: {len(failed)}",
        "",
        "## 明细",
        "",
        "| 文件 | 标题 | 效力 | 公布日期 | 施行日期 | 版本标识 | 处理 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        status = STATUS_LABELS.get(result.matched_sxx or result.desired_sxx, str(result.matched_sxx))
        title = result.official_title or result.requested_title
        lines.append(
            "| "
            + " | ".join(
                [
                    Path(result.path).name,
                    title,
                    status,
                    result.gbrq or "",
                    result.sxrq or "",
                    result.bbbs or "",
                    result.action if not result.error else f"{result.action}: {result.error}",
                ]
            )
            + " |"
        )
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--laws-dir", default="data/laws", help="directory with local .txt law files")
    parser.add_argument("--limit", type=int, default=0, help="process only the first N files")
    parser.add_argument("--dry-run", action="store_true", help="do not rewrite local law files")
    parser.add_argument("--report", action="store_true", help="write JSON and Markdown reports")
    parser.add_argument("--pause", type=float, default=0.25, help="pause between requests, in seconds")
    args = parser.parse_args(argv)

    laws_dir = Path(args.laws_dir)
    if not laws_dir.exists():
        print(f"laws dir not found: {laws_dir}", file=sys.stderr)
        return 2

    update_date = dt.datetime.now().strftime("%Y-%m-%d")
    files = sorted(laws_dir.glob("*.txt"))
    if args.limit:
        files = files[: args.limit]

    client = FlkClient(pause=args.pause)
    results: list[UpdateResult] = []
    for index, path in enumerate(files, start=1):
        result = update_one(client, path, update_date, args.dry_run)
        results.append(result)
        status = result.action
        if result.error:
            status += f" ({result.error})"
        print(f"[{index:02d}/{len(files):02d}] {path.name}: {status}")

    if args.report:
        write_reports(results, laws_dir, update_date)

    failed = [result for result in results if result.action in {"failed", "skipped"}]
    print(
        json.dumps(
            {
                "total": len(results),
                "failed_or_skipped": len(failed),
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
        )
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
