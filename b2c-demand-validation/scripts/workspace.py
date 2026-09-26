"""Workspace CLI: one folder per analysis, and an index of past analyses.

    python workspace.py new  --keywords "meal kits" "hellofresh" --projects en.wikipedia [--root analyses]
    python workspace.py part --id <id> --project de.wikipedia [--root analyses]
    python workspace.py list [--format short|full] [--limit 20] [--root analyses]
    python workspace.py show --id <id> [--root analyses]

Each analysis gets a folder named by a timestamp and a random id. Folder names
never carry the user's prompt. An analysis has one part per Wikimedia project:
each part has its own spec and results, named by code after the project
(`spec.en-wikipedia.json`, `results.en-wikipedia.json`), so the model never
types a file name. The report text and the PDF are shared by all parts.
`index.json` maps each id to the model's keywords and to metadata that code
reads from the folder's files, so the model can decide whether a new request
continues an old analysis or needs a new folder.

Exit codes: 0 on success, 2 on invalid input (nothing is written).
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
# Shared by all parts of an analysis.
FILES = {
    "text": "report.json",
    "pdf": "report.pdf",
}
# One pair per part; `{key}` is the part key, e.g. "en-wikipedia".
PART_FILES = {
    "spec": "spec.{key}.json",
    "results": "results.{key}.json",
}
# Folders created before parts existed hold a single spec and results.
LEGACY_PART_FILES = {
    "spec": "spec.json",
    "results": "results.json",
}
SPEC_FILE_PATTERN = re.compile(r"^spec\.(?P<key>[a-z0-9-]+)\.json$")
PROJECT_PATTERN = re.compile(r"^[a-z0-9-]+\.[a-z]+$")
MAX_KEYWORDS = 5
MAX_KEYWORD_CHARS = 30
MAX_PROJECTS = 5
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


def parse_project(value: str, field: str = "project") -> tuple[str, list[SpecError]]:
    project = value.strip().lower()
    if PROJECT_PATTERN.match(project):
        return project, []
    return project, [SpecError(field, "invalid_project", f'Use a project like "en.wikipedia"; got {value!r}.')]


def parse_projects(raw: list[str]) -> tuple[list[str], list[SpecError]]:
    projects: list[str] = []
    errors: list[SpecError] = []
    for i, value in enumerate(raw):
        project, project_errors = parse_project(value, f"projects[{i}]")
        errors += project_errors
        if not project_errors and project not in projects:
            projects.append(project)
    if not raw:
        errors.append(SpecError("projects", "missing", f'Give 1 to {MAX_PROJECTS} projects, e.g. "en.wikipedia".'))
    if len(projects) > MAX_PROJECTS:
        errors.append(SpecError("projects", "too_many", f"{len(projects)} projects; at most {MAX_PROJECTS}."))
    return projects, errors


# ---------------------------------------------------------------------------
# Parts: one per project
# ---------------------------------------------------------------------------

def part_key(project: str) -> str:
    """The key that names a part's files: "en.wikipedia" -> "en-wikipedia"."""
    return project.replace(".", "-")


def part_files(folder: Path, key: str) -> dict[str, Path]:
    return {name: folder / pattern.format(key=key) for name, pattern in PART_FILES.items()}


def add_part(folder: Path, project: str) -> dict[str, Any]:
    """Register a project as a part of the analysis (idempotent) and return the meta."""
    meta = read_json(folder / META_FILE)
    parts = dict(meta.get("parts") or {})
    if not parts:
        # A legacy folder: move its single spec and results to part names first, so they stay visible.
        for legacy in list_parts(folder, meta):
            for name, path in legacy["files"].items():
                if path.is_file():
                    os.replace(path, part_files(folder, legacy["key"])[name])
            parts[legacy["key"]] = {"project": legacy["project"], "lookup_rounds": legacy["lookup_rounds"]}
    key = part_key(project)
    if key not in parts:
        parts[key] = {"project": project, "lookup_rounds": 0}
    if parts != meta.get("parts"):
        meta = {**meta, "parts": parts}
        write_json(folder / META_FILE, meta)
    return meta


def list_parts(folder: Path, meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Every part of an analysis as `{key, project, lookup_rounds, files}`, in the order they were added.

    A folder from before parts existed is read as one part holding `spec.json`
    and `results.json`, keyed from the spec's project.
    """
    parts = [
        {
            "key": key,
            "project": part.get("project"),
            "lookup_rounds": part.get("lookup_rounds", 0),
            "files": part_files(folder, key),
        }
        for key, part in (meta.get("parts") or {}).items()
    ]
    legacy = {name: folder / file for name, file in LEGACY_PART_FILES.items()}
    if not parts and any(path.is_file() for path in legacy.values()):
        spec = read_json(legacy["spec"])
        project = spec.get("project") if isinstance(spec, dict) else None
        parts.append({
            "key": part_key(project) if isinstance(project, str) else "legacy",
            "project": project,
            "lookup_rounds": meta.get("lookup_rounds", 0),
            "files": legacy,
        })
    return parts


# ---------------------------------------------------------------------------
# Folders and index
# ---------------------------------------------------------------------------

def new_id(now: datetime) -> str:
    return f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def create_analysis(root: Path, keywords: list[str], projects: list[str], now: datetime) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for _ in range(ID_ATTEMPTS):
        folder = root / new_id(now)
        try:
            folder.mkdir()
        except FileExistsError:
            continue
        meta = {
            "id": folder.name,
            "created": now.isoformat(timespec="seconds"),
            "keywords": keywords,
            "parts": {part_key(p): {"project": p, "lookup_rounds": 0} for p in projects},
        }
        write_json(folder / META_FILE, meta)
        return folder
    raise RuntimeError(f"Could not create a unique analysis folder in {root} after {ID_ATTEMPTS} attempts.")


def analysis_folder(root: Path, analysis_id: str) -> Path | None:
    """The folder of an analysis, or None if the id is unknown."""
    folder = root / analysis_id
    if folder.parent != root or not isinstance(read_json(folder / META_FILE), dict):
        return None
    return folder


def file_paths(folder: Path) -> dict[str, Any]:
    """Absolute paths of the analysis's files: each part's by project, then the shared ones."""
    parts = {
        part["project"] or part["key"]: {name: str(path.resolve()) for name, path in part["files"].items()}
        for part in list_parts(folder, read_json(folder / META_FILE))
    }
    return {"parts": parts, **{key: str((folder / name).resolve()) for key, name in FILES.items()}}


def describe(folder: Path, meta: dict[str, Any]) -> dict[str, Any]:
    """Index entry for one folder: the model's keywords plus metadata read by code."""
    entry: dict[str, Any] = {
        "id": folder.name,
        "created": meta.get("created"),
        "keywords": meta.get("keywords", []),
        "projects": [],
        "period": None,
        "subjects": [],
        "metrics": [],
        "baseline": None,
        "parts": [],
        "has_report": (folder / FILES["pdf"]).is_file(),
        "updated": datetime.fromtimestamp(
            max(p.stat().st_mtime for p in [folder, *folder.iterdir()])
        ).isoformat(timespec="seconds"),
    }
    for part in list_parts(folder, meta):
        entry["projects"].append(part["project"])
        summary: dict[str, Any] = {
            "project": part["project"],
            "lookup_rounds": part["lookup_rounds"],
            "has_spec": part["files"]["spec"].is_file(),
            "results_status": None,
            "reliability": None,
        }
        spec = read_json(part["files"]["spec"])
        if isinstance(spec, dict):
            # All parts share one period and baseline; the first spec that has them sets them here.
            entry["period"] = entry["period"] or spec.get("period")
            entry["baseline"] = entry["baseline"] or spec.get("baseline")
            _extend_unique(entry["metrics"], spec.get("metrics") or [])
            _extend_unique(
                entry["subjects"], [s.get("label") for s in spec.get("subjects") or [] if isinstance(s, dict)]
            )
        results = read_json(part["files"]["results"])
        if isinstance(results, dict):
            summary["results_status"] = results.get("status")
            levels = results.get("summary")
            if isinstance(levels, dict):
                summary["reliability"] = {level: len(names) for level, names in levels.items()}
        entry["parts"].append(summary)
    return entry


def _extend_unique(target: list[Any], items: list[Any]) -> None:
    target.extend(item for item in items if item not in target)


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
        ", ".join(str(p) for p in entry["projects"]) or "-",
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


def _print_analysis(root: Path, entries: list[dict[str, Any]], analysis_id: str) -> int:
    entry = next((e for e in entries if e["id"] == analysis_id), None)
    if entry is None:
        return _invalid([SpecError("id", "unknown_id", f"No analysis {analysis_id!r} in {root}.")])
    folder = root / entry["id"]
    _print_json({"status": "ok", **entry, "dir": str(folder.resolve()), "files": file_paths(folder)})
    return EXIT_OK


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
    new.add_argument("--projects", nargs="+", required=True, help='1 to 5 projects, e.g. "en.wikipedia"')
    part = commands.add_parser("part", parents=[common], help="add a project to an existing analysis")
    part.add_argument("--id", required=True)
    part.add_argument("--project", required=True, help='e.g. "de.wikipedia"')
    listing = commands.add_parser("list", parents=[common], help="list past analyses, most recently updated first")
    listing.add_argument("--format", choices=["short", "full"], default="short")
    listing.add_argument("--limit", type=int, default=20)
    show = commands.add_parser("show", parents=[common], help="show one analysis and its file paths")
    show.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    root = Path(args.root)

    if args.command == "new":
        keywords, errors = parse_keywords(args.keywords)
        projects, project_errors = parse_projects(args.projects)
        errors += project_errors
        if errors:
            return _invalid(errors)
        folder = create_analysis(root, keywords, projects, datetime.now())
        refresh_index(root)
        _print_json({"status": "ok", "id": folder.name, "dir": str(folder.resolve()), "files": file_paths(folder)})
        return EXIT_OK

    if args.command == "part":
        project, errors = parse_project(args.project)
        folder = analysis_folder(root, args.id)
        if folder is None:
            errors.append(SpecError("id", "unknown_id", f"No analysis {args.id!r} in {root}."))
        else:
            parts = list_parts(folder, read_json(folder / META_FILE))
            if part_key(project) not in {p["key"] for p in parts} and len(parts) >= MAX_PROJECTS:
                errors.append(SpecError("project", "too_many", f"An analysis has at most {MAX_PROJECTS} projects."))
        if errors:
            return _invalid(errors)
        assert folder is not None
        add_part(folder, project)
        return _print_analysis(root, refresh_index(root), args.id)

    entries = refresh_index(root)

    if args.command == "list":
        entries = entries[: max(args.limit, 0)]
        if args.format == "short":
            print("\n".join(format_short(e) for e in entries) if entries else "No analyses yet.")
        else:
            _print_json({"status": "ok", "analyses": entries})
        return EXIT_OK

    return _print_analysis(root, entries, args.id)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
