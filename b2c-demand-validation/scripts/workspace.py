"""Workspace CLI: one folder per analysis, and an index of past analyses.

    python workspace.py new --keywords "meal kits" "hellofresh" [--root analyses]
    python workspace.py list [--format short|full] [--limit 20] [--root analyses]
    python workspace.py show --id <id> [--root analyses]

Each analysis gets a folder named by a timestamp and a random id, holding its
spec, results, report text and PDF under fixed names. Folder names never carry
the user's prompt. `index.json` maps each id to the model's keywords and to
metadata that code reads from the folder's files, so the model can decide
whether a new request continues an old analysis or needs a new folder.

Exit codes: 0 on success, 2 on invalid input (nothing is written).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from metrics_contracts import SpecError

EXIT_OK = 0
EXIT_INVALID_INPUT = 2

DEFAULT_ROOT = "analyses"
INDEX_FILE = "index.json"
META_FILE = "meta.json"
FILES = {
    "spec": "spec.json",
    "results": "results.json",
    "text": "report.json",
    "pdf": "report.pdf",
}
MAX_KEYWORDS = 5
MAX_KEYWORD_CHARS = 30
ID_ATTEMPTS = 20


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def parse_keywords(raw: list[str]) -> tuple[list[str], list[SpecError]]:
    keywords = [" ".join(k.split()).lower() for k in raw]
    keywords = list(dict.fromkeys(k for k in keywords if k))
    errors: list[SpecError] = []
    if not keywords:
        errors.append(SpecError("keywords", "missing", "Give 1 to 5 short keywords for the analysis topic."))
    if len(keywords) > MAX_KEYWORDS:
        errors.append(SpecError("keywords", "too_many", f"{len(keywords)} keywords; at most {MAX_KEYWORDS}."))
    for i, keyword in enumerate(keywords):
        if len(keyword) > MAX_KEYWORD_CHARS:
            errors.append(SpecError(
                f"keywords[{i}]", "too_long",
                f"{keyword!r} has {len(keyword)} characters; at most {MAX_KEYWORD_CHARS}.",
            ))
    return keywords, errors


# ---------------------------------------------------------------------------
# Folders and index
# ---------------------------------------------------------------------------

def new_id(now: datetime) -> str:
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def create_analysis(root: Path, keywords: list[str], now: datetime) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for _ in range(ID_ATTEMPTS):
        folder = root / new_id(now)
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        meta = {"id": folder.name, "created": now.isoformat(timespec="seconds"), "keywords": keywords}
        write_json(folder / META_FILE, meta)
        return folder
    raise RuntimeError(f"Could not create a unique analysis folder in {root} after {ID_ATTEMPTS} attempts.")


def analysis_folder(root: Path, analysis_id: str) -> Path | None:
    """The folder of an analysis, or None if the id is unknown."""
    folder = root / analysis_id
    if folder.parent != root or not isinstance(read_json(folder / META_FILE), dict):
        return None
    return folder


def file_paths(folder: Path) -> dict[str, str]:
    return {key: str((folder / name).resolve()) for key, name in FILES.items()}


def describe(folder: Path, meta: dict[str, Any]) -> dict[str, Any]:
    """Index entry for one folder: the model's keywords plus metadata read by code."""
    entry: dict[str, Any] = {
        "id": folder.name,
        "created": meta.get("created"),
        "keywords": meta.get("keywords", []),
        "project": None,
        "period": None,
        "subjects": [],
        "metrics": [],
        "baseline": None,
        "results_status": None,
        "reliability": None,
        "has_report": (folder / FILES["pdf"]).is_file(),
        "updated": datetime.fromtimestamp(
            max(p.stat().st_mtime for p in [folder, *folder.iterdir()])
        ).isoformat(timespec="seconds"),
    }
    spec = read_json(folder / FILES["spec"])
    if isinstance(spec, dict):
        entry["project"] = spec.get("project")
        entry["period"] = spec.get("period")
        entry["metrics"] = spec.get("metrics") or []
        entry["baseline"] = spec.get("baseline")
        entry["subjects"] = [
            s.get("label") for s in spec.get("subjects") or [] if isinstance(s, dict)
        ]
    results = read_json(folder / FILES["results"])
    if isinstance(results, dict):
        entry["results_status"] = results.get("status")
        summary = results.get("summary")
        if isinstance(summary, dict):
            entry["reliability"] = {level: len(names) for level, names in summary.items()}
    return entry


def refresh_index(root: Path) -> list[dict[str, Any]]:
    """Rebuild the index from the folders (the source of truth), most recently updated first."""
    entries = []
    if root.is_dir():
        for folder in root.iterdir():
            meta = read_json(folder / META_FILE) if folder.is_dir() else None
            if isinstance(meta, dict):
                entries.append(describe(folder, meta))
    entries.sort(key=lambda e: (e["updated"], e["id"]), reverse=True)
    if root.is_dir():
        write_json(root / INDEX_FILE, {"analyses": entries})
    return entries


def format_short(entry: dict[str, Any]) -> str:
    period = entry["period"]
    period_text = f"{period.get('start')}..{period.get('end')}" if isinstance(period, dict) else "-"
    return " | ".join([
        entry["id"],
        ", ".join(entry["keywords"]) or "-",
        entry["project"] or "-",
        period_text,
        ", ".join(str(s) for s in entry["subjects"]) or "-",
        ", ".join(entry["metrics"]) or "-",
        "report yes" if entry["has_report"] else "report no",
    ])


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def read_json(path: Path) -> Any:
    """Parse a JSON file, or None if it is missing or unreadable.

    `results.json` is often written by shell redirection, so a UTF-16 file
    (PowerShell's default) is accepted as well as UTF-8.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    encoding = "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    try:
        return json.loads(data.decode(encoding))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _invalid(errors: list[SpecError]) -> int:
    _print_json({"status": "invalid_input", "errors": [asdict(e) for e in errors]})
    return EXIT_INVALID_INPUT


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=DEFAULT_ROOT, help="folder that holds all analyses")
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", parents=[common], help="create a folder for a new analysis")
    new.add_argument("--keywords", nargs="+", required=True, help="1 to 5 short topic keywords")
    listing = commands.add_parser("list", parents=[common], help="list past analyses, most recently updated first")
    listing.add_argument("--format", choices=["short", "full"], default="short")
    listing.add_argument("--limit", type=int, default=20)
    show = commands.add_parser("show", parents=[common], help="show one analysis and its file paths")
    show.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.command == "new":
        keywords, errors = parse_keywords(args.keywords)
        if errors:
            return _invalid(errors)
        folder = create_analysis(root, keywords, datetime.now())
        refresh_index(root)
        _print_json({"status": "ok", "id": folder.name, "dir": str(folder.resolve()), "files": file_paths(folder)})
        return EXIT_OK

    entries = refresh_index(root)

    if args.command == "list":
        entries = entries[: max(args.limit, 0)]
        if args.format == "short":
            print("\n".join(format_short(e) for e in entries) if entries else "No analyses yet.")
        else:
            _print_json({"status": "ok", "analyses": entries})
        return EXIT_OK

    entry = next((e for e in entries if e["id"] == args.id), None)
    if entry is None:
        return _invalid([SpecError("id", "unknown_id", f"No analysis {args.id!r} in {root}.")])
    folder = root / entry["id"]
    _print_json({"status": "ok", **entry, "dir": str(folder.resolve()), "files": file_paths(folder)})
    return EXIT_OK


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
