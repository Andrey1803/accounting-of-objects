#!/usr/bin/env python3
"""
AI Workflow Optimizer v2.0.0 — утилита для оптимизации работы с AI-ассистентом в IDE.

Типичные проблемы, которые решает:
- Раздувание контекста (context window bloat)
- Повторные генерации одного и того же
- Ошибки валидации после правок ИИ
- Потеря архитектурных решений между сессиями

Новое в v2.0:
- --diff: сканирует только изменённые файлы из git
- rules.json: внешний конфиг правил (fallback на встроенные)
- --copy-context: копирует context.md в буфер обмена
- Метрики воркфлоу в отчёте
- Pre-commit hook для блокировки коммита с ошибками

Использует только stdlib (Python 3.8+).
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import io
import json
import logging
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Windows: force UTF-8 вывод
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

VERSION = "2.0.0"

# Паттерны директорий для игнорирования (всегда)
ALWAYS_IGNORE_DIRS: frozenset = frozenset({
    "bin", "obj", "node_modules", ".git", "venv", ".venv",
    "__pycache__", ".eggs", "dist", "build", ".mypy_cache",
    ".pytest_cache", ".tox", ".nox", ".hypothesis",
    "site-packages", ".idea", ".vscode",
})

# Паттерны файлов для игнорирования
ALWAYS_IGNORE_FILES: frozenset = frozenset({
    ".secret_key", ".env", ".env.local", "Thumbs.db",
    "desktop.ini", ".DS_Store",
})

# Расширения файлов, которые индексируются по умолчанию
INDEX_EXTENSIONS: frozenset = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".md", ".txt", ".sql", ".sh", ".bat", ".cmd",
    ".xml", ".cs", ".cshtml", ".csproj", ".sln", ".razor",
    ".env.example", ".gitignore",
})

# Расширения, которые считаются бинарными
BINARY_EXTENSIONS: frozenset = frozenset({
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".zip", ".tar",
    ".gz", ".bz2", ".xz", ".rar", ".7z", ".jpg", ".jpeg",
    ".png", ".gif", ".bmp", ".ico", ".svg", ".woff", ".woff2",
    ".ttf", ".eot", ".mp3", ".mp4", ".avi", ".mov", ".db",
    ".sqlite", ".sqlite3", ".xlsx", ".xls", ".doc", ".docx",
    ".pdf", ".class", ".jar", ".war", ".pdb", ".nupkg",
})

# Максимальный размер файла для чтения (100 КБ)
MAX_FILE_SIZE = 100 * 1024

# Максимальный размер context.md (50 КБ после сжатия)
MAX_CONTEXT_SIZE = 50 * 1024

# Максимальное количество файлов в context.md
MAX_FILES_IN_CONTEXT = 50

# Путь к файлу правил (рядом со скриптом)
DEFAULT_RULES_FILENAME = "rules.json"

# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ai_workflow_optimizer")

# ---------------------------------------------------------------------------
# Data-классы
# ---------------------------------------------------------------------------


@dataclass
class FileInfo:
    """Метаданные одного файла."""
    path: str
    size: int
    sha256: str
    extension: str
    line_count: int
    is_binary: bool = False
    is_truncated: bool = False
    content_preview: str = ""


@dataclass
class ProjectScan:
    """Результат сканирования проекта."""
    root: str
    total_files: int = 0
    total_lines: int = 0
    total_size: int = 0
    files_by_extension: dict = field(default_factory=dict)
    key_files: list = field(default_factory=list)
    all_files: list = field(default_factory=list)
    ignored_dirs: list = field(default_factory=list)
    ignored_files: list = field(default_factory=list)
    scan_time_ms: float = 0.0
    timestamp: str = ""


@dataclass
class ValidationIssue:
    """Проблема, найденная валидатором."""
    file: str
    line: int
    severity: str  # "error", "warning", "info"
    rule: str
    message: str


@dataclass
class ValidationReport:
    """Результат валидации."""
    issues: list = field(default_factory=list)
    files_checked: int = 0
    check_time_ms: float = 0.0
    timestamp: str = ""


@dataclass
class PromptEntry:
    """Запись в логе промптов."""
    timestamp: str
    direction: str  # "prompt" | "response"
    content_hash: str
    content_preview: str
    file_context: str = ""
    tokens_estimate: int = 0


@dataclass
class SessionState:
    """Состояние сессии для восстановления."""
    session_id: str
    started_at: str
    last_activity: str
    files_touched: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    prompt_count: int = 0
    response_count: int = 0


# ---------------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------------

def sha256_of_file(filepath: str) -> str:
    """Вычислить SHA-256 хеш файла."""
    h = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
    except (OSError, PermissionError) as exc:
        log.warning("Не удалось прочитать %s: %s", filepath, exc)
        return ""
    return h.hexdigest()


def safe_read_file(filepath: str, max_size: int = MAX_FILE_SIZE) -> Tuple[str, bool]:
    """Безопасно прочитать файл. Возвращает (содержимое, был_ли_обрезан)."""
    try:
        size = os.path.getsize(filepath)
    except OSError:
        return "", False

    if size == 0:
        return "", False

    if size > max_size:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_size)
            return content, True
        except (OSError, PermissionError):
            return "", False
    else:
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return content, False
        except (OSError, PermissionError):
            return "", False


def check_write_permission(directory: str) -> bool:
    """Проверить, что в директорию можно записывать."""
    try:
        test_file = os.path.join(directory, ".write_test_tmp")
        with open(test_file, "w") as f:
            f.write("")
        os.unlink(test_file)
        return True
    except (OSError, PermissionError):
        return False


def relative_path(filepath: str, root: str) -> str:
    """Получить относительный путь от корня проекта."""
    return os.path.relpath(filepath, root)


def is_binary_extension(filepath: str) -> bool:
    """Проверить, является ли файл бинарным по расширению."""
    ext = os.path.splitext(filepath)[1].lower()
    return ext in BINARY_EXTENSIONS


def count_lines(content: str) -> int:
    """Посчитать строки в содержимом."""
    if not content:
        return 0
    return content.count("\n") + 1


def estimate_tokens(text: str) -> int:
    """Грубая оценка количества токенов (1 токен ≈ 4 байта UTF-8)."""
    return max(1, len(text.encode("utf-8")) // 4)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def get_git_changed_files(root: str) -> Tuple[List[str], bool]:
    """
    Получить список изменённых файлов из git diff --name-only --diff-filter=AM.
    Возвращает (список_файлов, успех).
    Если git недоступен или не в репозитории — ([], False).
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=AM", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            # Может быть "not a git repository" или нет коммитов ещё
            return [], False
        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        return files, True
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return [], False


def copy_to_clipboard(text: str) -> bool:
    """
    Скопировать текст в буфер обмена.
    Windows: clip, macOS: pbcopy, Linux: xclip/xsel.
    """
    try:
        if sys.platform == "win32":
            # clip ожидает UTF-16-LE на stdin
            proc = subprocess.run(
                ["clip"],
                input=text.encode("utf-16-le"),
                timeout=10,
            )
            return proc.returncode == 0
        elif sys.platform == "darwin":
            proc = subprocess.run(
                ["pbcopy"],
                input=text.encode("utf-8"),
                timeout=10,
            )
            return proc.returncode == 0
        else:
            # Linux: пробуем xclip, потом xsel
            for cmd in [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]:
                try:
                    proc = subprocess.run(
                        cmd,
                        input=text.encode("utf-8"),
                        timeout=10,
                    )
                    if proc.returncode == 0:
                        return True
                except (FileNotFoundError, OSError):
                    continue
            return False
    except (subprocess.TimeoutExpired, OSError):
        return False


# ---------------------------------------------------------------------------
# Загрузчик правил (rules.json)
# ---------------------------------------------------------------------------

# Встроенные дефолтные правила (C# / ASP.NET Core / EF Core)
DEFAULT_RULES: Dict[str, Any] = {
    "errors": [
        {
            "name": "hardcoded-secret",
            "pattern": r"(?i)(password|passwd|pwd|api[_-]?key|apikey|secret[_-]?key|connection[_-]?string|database[_-]?url)\s*=\s*[\"'][^\"']{8,}[\"']",
            "message": "Хардкод секрета. Используй IConfiguration / appsettings.json + User Secrets / переменные окружения."
        },
        {
            "name": "syntax-error",
            "pattern": None,  # обрабатывается отдельно через AST/компилятор
            "message": "Синтаксическая ошибка. Проверь файл компилятором."
        },
        {
            "name": "model-routes",
            "pattern": r"@(page|controller|function)\s*\(",
            "message": "Модели/DTO не должны содержать атрибутов маршрутизации. Перемести в Controller."
        },
    ],
    "warnings": [
        {
            "name": "bare-except",
            "pattern": r"catch\s*\(\s*(Exception)?\s*\)\s*\{\s*\}",
            "message": "Пустой catch без обработки. Добавь логирование или throw."
        },
        {
            "name": "bare-except-py",
            "pattern": r"except\s*:\s*$",
            "message": "Bare except перехватывает ВСЕ исключения. Укажи тип."
        },
        {
            "name": "import-star",
            "pattern": r"(?i)using\s+static\s+\S+\.\*;",
            "message": "Импорт звёздочки загрязняет namespace. Импортируй явно."
        },
        {
            "name": "eval-exec",
            "pattern": r"\b(eval|exec)\s*\(",
            "message": "eval()/exec() — потенциальная уязвимость. Избегай."
        },
        {
            "name": "sync-db-call",
            "pattern": r"\.Result\b|\.Wait\(\)|\.GetAwaiter\(\)\.GetResult\(\)",
            "message": "Синхронное ожидание async-метода может вызвать deadlock. Используй await."
        },
        {
            "name": "controller-sql",
            "pattern": r"(?i)(DbContext|DbContextOptions|FromServices\s+DbContext)",
            "message": "Контроллер работает с DbContext напрямую. Вынеси в Service слой."
        },
        {
            "name": "hardcoded-url",
            "pattern": r"https?://[a-zA-Z0-9.-]+(?:\:\d+)?/[a-zA-Z0-9/._-]*",
            "message": "Хардкод URL. Вынеси в appsettings.json / IConfiguration."
        },
    ],
    "info": [
        {
            "name": "todo-fixme",
            "pattern": r"(?i)//\s*(TODO|FIXME|HACK|XXX|BUG|WORKAROUND)\s*:?\s*(.*)",
            "message": "Требует внимания."
        },
        {
            "name": "py-todo-fixme",
            "pattern": r"(?i)#\s*(TODO|FIXME|HACK|XXX|BUG|WORKAROUND)\s*:?\s*(.*)",
            "message": "Требует внимания."
        },
        {
            "name": "print-in-production",
            "pattern": r"print\s*\(",
            "message": "print() в продакшен-коде. Рекомендую logging / ILogger."
        },
        {
            "name": "console-writeline",
            "pattern": r"Console\.(Write|WriteLine)\s*\(",
            "message": "Console.WriteLine в продакшен-коде. Рекомендую ILogger<>."
        },
        {
            "name": "missing-nullable",
            "pattern": r"string\s+\w+\s*;",
            "message": "Не-nullable reference type без инициализации. Добавь '?' или '= null!'."
        },
    ],
}


class RuleLoader:
    """Загружает правила валидации из rules.json, дополняя дефолтные."""

    def __init__(self, rules_path: str):
        self.rules_path = os.path.abspath(rules_path)
        self.rules: Dict[str, Any] = DEFAULT_RULES
        self.loaded_external: bool = False

    def load(self) -> Dict[str, Any]:
        """Загрузить правила. Внешний файл дополняет/перезаписывает дефолтные."""
        if not os.path.isfile(self.rules_path):
            log.info("rules.json не найден — используются встроенные дефолтные правила")
            return self.rules

        try:
            with open(self.rules_path, "r", encoding="utf-8") as f:
                external = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("rules.json битый (%s) — используются встроенные дефолтные правила", exc)
            return self.rules

        # Валидация структуры
        if not isinstance(external, dict):
            log.warning("rules.json: ожидался объект — используются встроенные правила")
            return self.rules

        # Мердж: внешние правила дополняют/перезаписывают дефолтные по ключам
        for section in ("errors", "warnings", "info"):
            if section in external and isinstance(external[section], list):
                # Проверяем каждое правило
                valid_rules = []
                for rule in external[section]:
                    if isinstance(rule, dict) and "name" in rule and "pattern" in rule and "message" in rule:
                        valid_rules.append(rule)
                    else:
                        log.warning("Пропущено битое правило в секции %s: %s", section, rule)
                # Заменяем дефолтные для этой секции внешними
                self.rules[section] = valid_rules
                self.loaded_external = True

        log.info("Загружены внешние правила из rules.json (errors: %d, warnings: %d, info: %d)",
                 len(self.rules.get("errors", [])),
                 len(self.rules.get("warnings", [])),
                 len(self.rules.get("info", [])))
        return self.rules


# ---------------------------------------------------------------------------
# Сканер проекта
# ---------------------------------------------------------------------------

class ProjectScanner:
    """Сканирует проект, формирует структуру и метаданные."""

    def __init__(self, root: str, extra_ignores: Optional[List[str]] = None,
                 diff_files: Optional[List[str]] = None):
        self.root = os.path.abspath(root)
        self.extra_ignores: set = set(extra_ignores or [])
        self.gitignore_patterns: List[str] = []
        self.diff_files: Optional[set] = set(diff_files) if diff_files else None
        self._load_gitignore()

    def _load_gitignore(self) -> None:
        """Загрузить паттерны из .gitignore."""
        gitignore_path = os.path.join(self.root, ".gitignore")
        if os.path.isfile(gitignore_path):
            try:
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            self.gitignore_patterns.append(line)
            except (OSError, PermissionError):
                log.warning("Не удалось прочитать .gitignore")

    def _should_ignore_dir(self, dirname: str) -> bool:
        """Проверить, нужно ли игнорировать директорию."""
        if dirname in ALWAYS_IGNORE_DIRS:
            return True
        for pattern in self.gitignore_patterns:
            if fnmatch.fnmatch(dirname, pattern.rstrip("/")):
                return True
        for pattern in self.extra_ignores:
            if fnmatch.fnmatch(dirname, pattern):
                return True
        return False

    def _should_ignore_file(self, filename: str, relpath: str) -> bool:
        """Проверить, нужно ли игнорировать файл."""
        if filename in ALWAYS_IGNORE_FILES:
            return True
        # В режиме --diff игнорируем всё, кроме изменённых файлов
        if self.diff_files is not None and relpath not in self.diff_files:
            return True
        for pattern in self.gitignore_patterns:
            if fnmatch.fnmatch(relpath, pattern):
                return True
            if fnmatch.fnmatch(filename, pattern):
                return True
        for pattern in self.extra_ignores:
            if fnmatch.fnmatch(relpath, pattern):
                return True
        return False

    def scan(self) -> ProjectScan:
        """Выполнить сканирование проекта (полное или только diff-файлы)."""
        start = time.monotonic()
        scan = ProjectScan(
            root=self.root,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if not os.path.isdir(self.root):
            log.error("Директория не существует: %s", self.root)
            return scan

        for dirpath, dirnames, filenames in os.walk(self.root):
            # Фильтрация директорий "на месте"
            dirnames[:] = [
                d for d in dirnames
                if not self._should_ignore_dir(d)
            ]
            # Запоминаем пропущенные директории
            try:
                ignored_in_level = [
                    d for d in os.listdir(dirpath)
                    if os.path.isdir(os.path.join(dirpath, d))
                    and self._should_ignore_dir(d)
                ]
                scan.ignored_dirs.extend(
                    relative_path(os.path.join(dirpath, d), self.root)
                    for d in ignored_in_level
                )
            except OSError:
                pass

            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                relpath = relative_path(fpath, self.root)

                if self._should_ignore_file(fname, relpath):
                    if self.diff_files is None:
                        scan.ignored_files.append(relpath)
                    continue

                try:
                    fsize = os.path.getsize(fpath)
                except OSError:
                    continue

                ext = os.path.splitext(fname)[1].lower()
                is_bin = is_binary_extension(fpath)

                info = FileInfo(
                    path=relpath,
                    size=fsize,
                    sha256=sha256_of_file(fpath),
                    extension=ext,
                    line_count=0,
                    is_binary=is_bin,
                )

                if not is_bin and ext in INDEX_EXTENSIONS:
                    content, truncated = safe_read_file(fpath)
                    info.line_count = count_lines(content)
                    info.is_truncated = truncated
                    info.content_preview = content[:200].replace("\n", " ").strip()
                    scan.total_lines += info.line_count

                scan.total_files += 1
                scan.total_size += fsize
                scan.files_by_extension[ext] = \
                    scan.files_by_extension.get(ext, 0) + 1
                scan.all_files.append(info)

        # Ключевые файлы
        scan.key_files = self._identify_key_files(scan.all_files)
        scan.scan_time_ms = (time.monotonic() - start) * 1000
        return scan

    def _identify_key_files(self, all_files: List[FileInfo]) -> List[FileInfo]:
        """Определить ключевые файлы проекта."""
        key_patterns = [
            r"^app\.py$", r"^main\.py$", r"^index\.py$",
            r"^manage\.py$", r"^wsgi\.py$", r"^asgi\.py$",
            r"^package\.json$", r"^requirements\.txt$",
            r"^setup\.py$", r"^pyproject\.toml$",
            r"^Cargo\.toml$", r"^pom\.xml$", r"^build\.gradle$",
            r"^\.gitignore$", r"^README\.md$",
            r"^database\.py$", r"^auth\.py$",
            r"^config\.", r"^settings\.",
            # C# / ASP.NET Core
            r"^Program\.cs$", r"^Startup\.cs$",
            r"^appsettings\.json$", r"^appsettings\.\w+\.json$",
            r"^.*\.csproj$", r"^.*\.sln$",
        ]
        key_files = []
        for finfo in all_files:
            basename = os.path.basename(finfo.path)
            for pattern in key_patterns:
                if re.search(pattern, basename, re.IGNORECASE):
                    key_files.append(finfo)
                    break
        priority_order = [
            "Program.cs", "app.py", "main.py", "Startup.cs", "wsgi.py",
            "package.json", "requirements.txt", "pyproject.toml",
            "database.py", "auth.py", "appsettings.json",
            ".gitignore", "README.md",
        ]
        key_files.sort(
            key=lambda f: (
                priority_order.index(os.path.basename(f.path))
                if os.path.basename(f.path) in priority_order
                else len(priority_order)
            )
        )
        return key_files[:20]

    @classmethod
    def scan_diff(cls, root: str, extra_ignores: Optional[List[str]] = None) -> Tuple[ProjectScan, bool]:
        """
        Сканировать только изменённые файлы из git diff.
        Возвращает (скан, был_ли_diff_успешен).
        Если diff не удался — fallback на полный скан.
        """
        diff_files, ok = get_git_changed_files(root)
        if ok and diff_files:
            # Нормализуем пути (git отдаёт с /, Windows может дать \\)
            normalized = [f.replace("/", os.sep) for f in diff_files]
            scanner = cls(root, extra_ignores=extra_ignores, diff_files=normalized)
            scan = scanner.scan()
            return scan, True
        else:
            # Fallback на полный скан
            scanner = cls(root, extra_ignores=extra_ignores, diff_files=None)
            scan = scanner.scan()
            return scan, False


# ---------------------------------------------------------------------------
# Генератор context.md
# ---------------------------------------------------------------------------

class ContextBuilder:
    """Формирует сжатый context.md для AI-ассистента."""

    def __init__(self, scan: ProjectScan, root: str):
        self.scan = scan
        self.root = root

    def build(self, compress: bool = False) -> str:
        """Сгенерировать содержимое context.md."""
        sections: List[str] = []

        sections.append(self._header())
        sections.append(self._project_tree())
        sections.append(self._extension_stats())
        sections.append(self._key_files_content(compress))
        sections.append(self._inferred_rules())
        sections.append(self._recent_changes())

        content = "\n\n".join(sections)

        if compress and len(content.encode("utf-8")) > MAX_CONTEXT_SIZE:
            content = self._compress(content)

        return content

    def _header(self) -> str:
        """Секция: метаинформация."""
        return (
            f"# Project Context\n\n"
            f"**Generated:** {self.scan.timestamp}\n"
            f"**Root:** `{self.scan.root}`\n"
            f"**Total files:** {self.scan.total_files}\n"
            f"**Total lines:** {self.scan.total_lines:,}\n"
            f"**Total size:** {self.scan.total_size / 1024:.1f} KB\n"
            f"**Scan time:** {self.scan.scan_time_ms:.0f} ms"
        )

    def _project_tree(self) -> str:
        """Секция: дерево проекта (упрощённое)."""
        tree_lines = ["## Project Structure\n", "```"]
        dir_set: set = set()
        for finfo in self.scan.all_files:
            parts = finfo.path.replace("\\", "/").split("/")
            for i in range(1, len(parts)):
                dir_set.add("/".join(parts[:i]))

        all_entries = sorted(dir_set | {f.path.replace("\\", "/") for f in self.scan.all_files})
        for entry in all_entries:
            parts = entry.split("/")
            depth = len(parts)
            indent = "  " * (depth - 1)
            name = parts[-1]
            is_dir = entry in dir_set
            prefix = "[DIR] " if is_dir else "[FILE] "
            tree_lines.append(f"{indent}{prefix}{name}")
        tree_lines.append("```")
        return "\n".join(tree_lines)

    def _extension_stats(self) -> str:
        """Секция: статистика по расширениям."""
        lines = ["## File Types\n"]
        for ext, count in sorted(
            self.scan.files_by_extension.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            lines.append(f"- `{ext or '(no ext)'}`: {count}")
        return "\n".join(lines)

    def _key_files_content(self, compress: bool) -> str:
        """Секция: содержимое ключевых файлов."""
        lines = ["## Key Files\n"]
        count = 0
        max_chars = MAX_CONTEXT_SIZE // 2

        for finfo in self.scan.key_files:
            if count >= MAX_FILES_IN_CONTEXT:
                break
            if compress:
                lines.append(
                    f"### `{finfo.path}`\n"
                    f"- Lines: {finfo.line_count} | "
                    f"Size: {finfo.size} bytes | "
                    f"SHA256: `{finfo.sha256[:12]}...`\n"
                )
            else:
                fpath = os.path.join(self.root, finfo.path)
                content, truncated = safe_read_file(fpath)
                if content:
                    max_per_file = max_chars // MAX_FILES_IN_CONTEXT
                    if len(content) > max_per_file:
                        content = content[:max_per_file] + "\n\n--- [truncated] ---"
                    ext_label = finfo.extension.lstrip(".") or "text"
                    lines.append(
                        f"### `{finfo.path}`\n"
                        f"```{ext_label}\n"
                        f"{content}\n"
                        f"```\n"
                    )
            count += 1

        if count >= MAX_FILES_IN_CONTEXT:
            lines.append(
                f"\n> ... ещё {len(self.scan.key_files) - count} файлов "
                f"пропущено (лимит {MAX_FILES_IN_CONTEXT})"
            )

        return "\n".join(lines)

    def _inferred_rules(self) -> str:
        """Секция: автоматически выведенные правила."""
        rules: List[str] = []

        has_flask = any(
            f.path.endswith(".py") and "flask" in f.content_preview.lower()
            for f in self.scan.all_files
        )
        has_django = any(
            f.path.endswith(".py") and "django" in f.content_preview.lower()
            for f in self.scan.all_files
        )
        has_react = any(
            f.path.endswith((".js", ".jsx", ".ts", ".tsx"))
            and "react" in f.content_preview.lower()
            for f in self.scan.all_files
        )
        has_aspnet = any(
            f.path.endswith(".cs") and (
                "Controller" in f.content_preview or
                "IActionResult" in f.content_preview or
                "Microsoft.AspNetCore" in f.content_preview
            )
            for f in self.scan.all_files
        )
        has_efcore = any(
            f.path.endswith(".cs") and (
                "DbContext" in f.content_preview or
                "Microsoft.EntityFrameworkCore" in f.content_preview
            )
            for f in self.scan.all_files
        )
        has_blazor = any(
            f.path.endswith(".razor") or (
                f.path.endswith(".cshtml") and "Razor" in f.content_preview
            )
            for f in self.scan.all_files
        )

        if has_flask:
            rules.append("- **Flask**: маршруты в blueprint, шаблоны в templates/, статика в static/")
        if has_django:
            rules.append("- **Django**: Models → Views → Templates, миграции через makemigrations/migrate")
        if has_react:
            rules.append("- **React**: компоненты в src/components, хуки, функциональные компоненты")
        if has_aspnet:
            rules.append(
                "- **ASP.NET Core**: Controllers → Services → DTO → Repository. "
                "Маршруты через [HttpGet], [HttpPost] и т.д. Модели в Models/, DTO в Dtos/, сервисы в Services/."
            )
        if has_efcore:
            rules.append(
                "- **EF Core**: DbContext в Infrastructure/, миграции через `dotnet ef migrations add`. "
                "Не использовать .Result / .Wait() — только async/await."
            )
        if has_blazor:
            rules.append(
                "- **Blazor/Razor**: компоненты .razor в Pages/ и Components/, "
                "code-behind .razor.cs, @inject для DI, EventCallback для событий."
            )

        has_sqlite = any(
            f.path.endswith(".py") and "sqlite" in f.content_preview.lower()
            for f in self.scan.all_files
        )
        if has_sqlite:
            rules.append("- **SQLite**: локальная БД, sqlite3 module")

        has_auth = any(
            "auth" in os.path.basename(f.path).lower()
            for f in self.scan.all_files
        )
        if has_auth:
            rules.append("- **Auth**: модуль auth.py, сессии, login_required")

        if not rules:
            rules.append("- Стандартная структура проекта, следуй существующим конвенциям")

        rules.extend([
            "- **НЕ** хардкодь секреты — используй .env / User Secrets / IConfiguration",
            "- **НЕ** оставляй пустые catch/except — всегда логируй ошибку",
            "- **НЕ** изменяй .gitignore без явной просьбы",
            "- **СОХРАНЯЙ** обратную совместимость API",
            "- **ДОБАВЛЯЙ** type hints (Python) / nullable reference types (C#) в новый код",
        ])

        return "## Inferred Project Rules\n\n" + "\n".join(rules)

    def _recent_changes(self) -> str:
        """Секция: последние изменения (если есть .git)."""
        git_dir = os.path.join(self.root, ".git")
        if not os.path.isdir(git_dir):
            return "## Recent Changes\n\n> Git не обнаружен. История изменений недоступна."

        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-10", "--no-decorate"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                lines = ["## Recent Changes\n", "```", result.stdout.strip(), "```"]
                return "\n".join(lines)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        return "## Recent Changes\n\n> Не удалось получить историю git."

    def _compress(self, content: str) -> str:
        """Сжать контент: удалить пустые строки, сократить превью."""
        lines = content.split("\n")
        compressed: List[str] = []
        in_code_block = False
        code_block_lines = 0

        for line in lines:
            if not line.strip():
                if compressed and not compressed[-1].strip():
                    continue
                compressed.append(line)
                continue

            if line.strip().startswith("```"):
                if in_code_block:
                    compressed.append(line)
                    in_code_block = False
                    code_block_lines = 0
                else:
                    compressed.append(line)
                    in_code_block = True
                continue

            if in_code_block:
                code_block_lines += 1
                if code_block_lines > 30:
                    if compressed[-1] != "> [compressed: ...]":
                        compressed.append("> [compressed: truncated for size]")
                    continue

            compressed.append(line)

        return "\n".join(compressed)


# ---------------------------------------------------------------------------
# Валидатор кода (типовые ошибки ИИ)
# ---------------------------------------------------------------------------

class CodeValidator:
    """Проверяет код на типовые ошибки, допускаемые ИИ-ассистентами."""

    def __init__(self, root: str, rules: Optional[Dict[str, Any]] = None):
        self.root = os.path.abspath(root)
        self.rules = rules or DEFAULT_RULES

    def validate(self, files: Optional[List[FileInfo]] = None) -> ValidationReport:
        """Запустить полную валидацию."""
        start = time.monotonic()
        report = ValidationReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        if files is None:
            scanner = ProjectScanner(self.root)
            scan = scanner.scan()
            files = [
                f for f in scan.all_files
                if not f.is_binary and f.extension in (".py", ".cs")
            ]

        for finfo in files:
            fpath = os.path.join(self.root, finfo.path)
            content, _ = safe_read_file(fpath)
            if not content:
                continue

            report.files_checked += 1

            # Python-специфичные проверки
            if finfo.extension == ".py":
                report.issues.extend(self._check_empty_except(finfo.path, content))
                report.issues.extend(self._check_bare_except(finfo.path, content))
                report.issues.extend(self._check_print_statements(finfo.path, content))
                report.issues.extend(self._check_py_todo_fixme(finfo.path, content))
                report.issues.extend(self._check_eval_exec(finfo.path, content))
                report.issues.extend(self._check_import_star_py(finfo.path, content))
                report.issues.extend(self._check_syntax_py(finfo.path, content))
                report.issues.extend(self._check_unreachable_code(finfo.path, content))
                report.issues.extend(self._check_architecture_py(finfo.path, content))
            # C#-специфичные проверки
            elif finfo.extension == ".cs":
                report.issues.extend(self._check_cs_empty_catch(finfo.path, content))
                report.issues.extend(self._check_cs_console_writeline(finfo.path, content))
                report.issues.extend(self._check_cs_todo_fixme(finfo.path, content))
                report.issues.extend(self._check_cs_sync_db_calls(finfo.path, content))
                report.issues.extend(self._check_cs_architecture(finfo.path, content))
                report.issues.extend(self._check_cs_missing_nullable(finfo.path, content))
            # Общие проверки из rules.json
            report.issues.extend(self._check_rules_json_patterns(finfo.path, content))
            # Универсальная проверка секретов (regex)
            report.issues.extend(self._check_hardcoded_secrets(finfo.path, content))

        report.check_time_ms = (time.monotonic() - start) * 1000
        return report

    # ---- Python-специфичные проверки ----

    def _check_empty_except(self, filepath: str, content: str) -> List[ValidationIssue]:
        """Python: пустые except/catch без обработки."""
        issues: List[ValidationIssue] = []
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if re.match(r"except\s*[\w\s,]*:\s*pass\s*$", stripped):
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="warning",
                    rule="empty-except-py",
                    message="Пустой except (pass) — ошибка будет проглочена. Добавь logging.",
                ))
        return issues

    def _check_bare_except(self, filepath: str, content: str) -> List[ValidationIssue]:
        """Python: bare except: без указания типа."""
        # Пропускаем если правило уже в rules.json
        if any(r.get("name") == "bare-except-py" for r in self.rules.get("warnings", [])):
            return []
        issues: List[ValidationIssue] = []
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if re.match(r"except\s*:\s*$", stripped) or re.match(r"except\s*:\s*\w+", stripped):
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="warning",
                    rule="bare-except-py",
                    message="Bare except перехватывает ВСЕ исключения, включая KeyboardInterrupt.",
                ))
        return issues

    def _check_print_statements(self, filepath: str, content: str) -> List[ValidationIssue]:
        """Python: print() в продакшен-коде."""
        issues: List[ValidationIssue] = []
        basename = os.path.basename(filepath)
        if basename.startswith(("check_", "clean_", "test_")):
            return issues
        for i, line in enumerate(content.split("\n"), 1):
            if re.match(r"\s*print\s*\(", line):
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="info",
                    rule="print-in-production",
                    message="print() в продакшен-коде. Используй logging.",
                ))
        return issues

    def _check_py_todo_fixme(self, filepath: str, content: str) -> List[ValidationIssue]:
        """Python: TODO/FIXME/HACK."""
        issues: List[ValidationIssue] = []
        for i, line in enumerate(content.split("\n"), 1):
            match = re.search(r"(?i)#\s*(TODO|FIXME|HACK|XXX|BUG|WORKAROUND)\s*:?\s*(.*)", line.strip())
            if match:
                tag = match.group(1).upper()
                desc = match.group(2).strip()
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="info",
                    rule=f"todo-{tag.lower()}",
                    message=f"{tag}: {desc}" if desc else f"{tag} — требует внимания",
                ))
        return issues

    def _check_eval_exec(self, filepath: str, content: str) -> List[ValidationIssue]:
        """Python: eval()/exec()."""
        issues: List[ValidationIssue] = []
        basename = os.path.basename(filepath)
        if basename in ("ai_workflow_optimizer.py",) or basename.startswith("test_"):
            return issues
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if not stripped.startswith("#"):
                if re.search(r"\beval\s*\(", stripped):
                    issues.append(ValidationIssue(
                        file=filepath, line=i, severity="error",
                        rule="use-of-eval",
                        message="eval() — потенциальная уязвимость безопасности.",
                    ))
                if re.search(r"\bexec\s*\(", stripped):
                    issues.append(ValidationIssue(
                        file=filepath, line=i, severity="warning",
                        rule="use-of-exec",
                        message="exec() — потенциальная уязвимость.",
                    ))
        return issues

    def _check_import_star_py(self, filepath: str, content: str) -> List[ValidationIssue]:
        """Python: from module import *."""
        issues: List[ValidationIssue] = []
        for i, line in enumerate(content.split("\n"), 1):
            if re.match(r"from\s+\w+\s+import\s+\*$", line.strip()):
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="warning",
                    rule="import-star-py",
                    message="`from module import *` загрязняет namespace.",
                ))
        return issues

    def _check_syntax_py(self, filepath: str, content: str) -> List[ValidationIssue]:
        """Python: синтаксическая корректность."""
        issues: List[ValidationIssue] = []
        if os.path.splitext(filepath)[1] != ".py":
            return issues
        try:
            ast.parse(content)
        except SyntaxError as exc:
            issues.append(ValidationIssue(
                file=filepath, line=exc.lineno or 1, severity="error",
                rule="syntax-error-py",
                message=f"Синтаксическая ошибка: {exc.msg}",
            ))
        except Exception:
            pass
        return issues

    def _check_unreachable_code(self, filepath: str, content: str) -> List[ValidationIssue]:
        """Python: недостижимый код после return/raise/break."""
        issues: List[ValidationIssue] = []
        if os.path.splitext(filepath)[1] != ".py":
            return issues
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return issues
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._check_function_body(node, content, issues)
        return issues

    def _check_function_body(
        self,
        func_node,  # ast.FunctionDef | ast.AsyncFunctionDef
        content: str,
        issues: List[ValidationIssue],
    ) -> None:
        """Проверить тело функции на недостижимый код."""
        terminal_nodes = (ast.Return, ast.Raise, ast.Break, ast.Continue)
        for i, body_item in enumerate(func_node.body[:-1]):
            if isinstance(body_item, ast.Expr) and isinstance(body_item.value, ast.Constant):
                continue  # docstring
            if isinstance(body_item, terminal_nodes):
                next_item = func_node.body[i + 1]
                if isinstance(next_item, ast.Expr) and isinstance(next_item.value, ast.Constant):
                    continue  # docstring после return
                try:
                    line_no = body_item.end_lineno or body_item.lineno
                    if line_no:
                        issues.append(ValidationIssue(
                            file=os.path.basename(func_node.name) if hasattr(func_node, 'name') else filepath,
                            line=line_no, severity="warning",
                            rule="unreachable-code",
                            message="Код после return/raise/break недостижим.",
                        ))
                except Exception:
                    pass

    def _check_architecture_py(self, filepath: str, content: str) -> List[ValidationIssue]:
        """Python: нарушение архитектурных паттернов."""
        issues: List[ValidationIssue] = []
        basename = os.path.basename(filepath)
        if re.match(r"(views|routes|controllers?|api)\.py", basename, re.I):
            if re.search(r"(execute|cursor\.execute|\.execute\s*\()", content):
                issues.append(ValidationIssue(
                    file=filepath, line=1, severity="warning",
                    rule="controller-sql",
                    message="Контроллер содержит SQL-запросы. Вынеси в Service/Repository.",
                ))
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        func_lines = (node.end_lineno or node.lineno) - node.lineno
                        if func_lines > 50:
                            issues.append(ValidationIssue(
                                file=filepath, line=node.lineno, severity="info",
                                rule="controller-complexity",
                                message=f"Функция '{node.name}' слишком длинная ({func_lines} строк). "
                                        f"Вынеси бизнес-логику в Service слой.",
                            ))
            except SyntaxError:
                pass
        if re.match(r"(models?|entities?)\.py", basename, re.I):
            if re.search(r"@app\.route|@blueprint\.route|@.*\.route", content):
                issues.append(ValidationIssue(
                    file=filepath, line=1, severity="error",
                    rule="model-routes",
                    message="Модели не должны содержать маршруты. Перемести в контроллер.",
                ))
        return issues

    # ---- C#-специфичные проверки ----

    def _check_cs_empty_catch(self, filepath: str, content: str) -> List[ValidationIssue]:
        """C#: пустой catch {}."""
        issues: List[ValidationIssue] = []
        for i, line in enumerate(content.split("\n"), 1):
            if re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", line):
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="warning",
                    rule="empty-catch-cs",
                    message="Пустой catch — ошибка будет проглочена. Добавь логирование или throw.",
                ))
        return issues

    def _check_cs_console_writeline(self, filepath: str, content: str) -> List[ValidationIssue]:
        """C#: Console.WriteLine в продакшен-коде."""
        basename = os.path.basename(filepath)
        if basename.startswith(("test_", "Test")) or "Program.cs" in basename:
            return []
        issues: List[ValidationIssue] = []
        for i, line in enumerate(content.split("\n"), 1):
            if re.search(r"Console\.(Write|WriteLine)\s*\(", line):
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="info",
                    rule="console-writeline",
                    message="Console.WriteLine в продакшен-коде. Используй ILogger<>.",
                ))
        return issues

    def _check_cs_todo_fixme(self, filepath: str, content: str) -> List[ValidationIssue]:
        """C#: TODO/FIXME/HACK."""
        issues: List[ValidationIssue] = []
        for i, line in enumerate(content.split("\n"), 1):
            match = re.search(r"(?i)//\s*(TODO|FIXME|HACK|XXX|BUG|WORKAROUND)\s*:?\s*(.*)", line.strip())
            if match:
                tag = match.group(1).upper()
                desc = match.group(2).strip()
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="info",
                    rule=f"todo-{tag.lower()}",
                    message=f"{tag}: {desc}" if desc else f"{tag} — требует внимания",
                ))
        return issues

    def _check_cs_sync_db_calls(self, filepath: str, content: str) -> List[ValidationIssue]:
        """C#: синхронное ожидание async (.Result, .Wait())."""
        issues: List[ValidationIssue] = []
        for i, line in enumerate(content.split("\n"), 1):
            if re.search(r"\.Result\b", line) and not line.strip().startswith("//"):
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="warning",
                    rule="sync-db-call",
                    message=".Result может вызвать deadlock. Используй await.",
                ))
            if re.search(r"\.Wait\(\)", line) and not line.strip().startswith("//"):
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="warning",
                    rule="sync-wait",
                    message=".Wait() блокирует поток. Используй await.",
                ))
            if re.search(r"\.GetAwaiter\(\)\.GetResult\(\)", line) and not line.strip().startswith("//"):
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="warning",
                    rule="getawaiter-getresult",
                    message=".GetAwaiter().GetResult() блокирует поток. Используй await.",
                ))
        return issues

    def _check_cs_architecture(self, filepath: str, content: str) -> List[ValidationIssue]:
        """C#: нарушение архитектуры (DbContext в Controller, маршруты в Model)."""
        issues: List[ValidationIssue] = []
        basename = os.path.basename(filepath)

        # Контроллеры не должны работать с DbContext напрямую
        if re.search(r"Controller\.cs$", basename):
            if re.search(r"(DbContext|_context\.)", content):
                issues.append(ValidationIssue(
                    file=filepath, line=1, severity="warning",
                    rule="controller-dbcontext",
                    message="Контроллер использует DbContext напрямую. Вынеси в Service слой.",
                ))
            # Проверка на длинную функцию в контроллере
            if re.search(r"(public\s+async\s+Task<\w+>\s+\w+|public\s+\w+Result\s+\w+)\s*\(", content):
                # Грубая проверка: если файл контроллера > 500 строк
                line_count = content.count("\n") + 1
                if line_count > 500:
                    issues.append(ValidationIssue(
                        file=filepath, line=1, severity="info",
                        rule="controller-size",
                        message=f"Контроллер слишком большой ({line_count} строк). Разбей на сервисы.",
                    ))

        # Модели не должны содержать атрибуты маршрутизации
        if re.search(r"(Model|Entity|Dto)\.cs$", basename):
            if re.search(r"@(page|controller|function)\s*\(", content):
                issues.append(ValidationIssue(
                    file=filepath, line=1, severity="error",
                    rule="model-routes",
                    message="Модели/DTO не должны содержать атрибутов маршрутизации.",
                ))

        return issues

    def _check_cs_missing_nullable(self, filepath: str, content: str) -> List[ValidationIssue]:
        """C#: reference type без инициализации."""
        issues: List[ValidationIssue] = []
        for i, line in enumerate(content.split("\n"), 1):
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*"):
                continue
            # public string Name; (без = и без ?)
            if re.search(r"public\s+string\s+\w+\s*;", stripped):
                issues.append(ValidationIssue(
                    file=filepath, line=i, severity="info",
                    rule="missing-nullable",
                    message="Не-nullable reference type без инициализации. Добавь '?' или '= null!;'.",
                ))
        return issues

    # ---- Общие проверки из rules.json ----

    def _check_rules_json_patterns(self, filepath: str, content: str) -> List[ValidationIssue]:
        """Применить паттерны из загруженных rules.json."""
        issues: List[ValidationIssue] = []
        basename = os.path.basename(filepath)
        if basename == "ai_workflow_optimizer.py":
            return issues

        for section in ("errors", "warnings", "info"):
            for rule in self.rules.get(section, []):
                pattern = rule.get("pattern")
                if not pattern:
                    continue  # pattern=None — это метка для спец-проверок (AST и т.д.)
                try:
                    for i, line in enumerate(content.split("\n"), 1):
                        if re.search(pattern, line):
                            issues.append(ValidationIssue(
                                file=filepath, line=i, severity=section[:-1],  # "errors"→"error"
                                rule=rule["name"],
                                message=rule["message"],
                            ))
                except re.error:
                    log.warning("Битый regex в правиле '%s': %s", rule.get("name", "?"), pattern)
        return issues

    def _check_hardcoded_secrets(self, filepath: str, content: str) -> List[ValidationIssue]:
        """Универсальная проверка: хардкод секретов."""
        issues: List[ValidationIssue] = []
        basename = os.path.basename(filepath)
        if basename == "ai_workflow_optimizer.py":
            return issues

        secret_patterns = [
            (r"(?i)(password|passwd|pwd)\s*=\s*['\"][^'\"]{3,}['\"]", "Хардкод пароля"),
            (r"(?i)(api[_-]?key|apikey)\s*=\s*['\"][^'\"]{8,}['\"]", "Хардкод API-ключа"),
            (r"(?i)(secret[_-]?key|secret)\s*=\s*['\"][^'\"]{8,}['\"]", "Хардкод секрета"),
            (r"(?i)(token|auth[_-]?token|access[_-]?token)\s*=\s*['\"][^'\"]{8,}['\"]", "Хардкод токена"),
            (r"(?i)(connection[_-]?string|database[_-]?url)\s*=\s*['\"][^'\"]*:[^'\"]*@['\"]",
             "Connection string с паролем"),
        ]
        for i, line in enumerate(content.split("\n"), 1):
            for pattern, description in secret_patterns:
                if re.search(pattern, line):
                    if os.path.basename(filepath) in (".env.example",):
                        continue
                    issues.append(ValidationIssue(
                        file=filepath, line=i, severity="error",
                        rule="hardcoded-secret",
                        message=f"{description}. Используй os.environ.get() / IConfiguration / User Secrets.",
                    ))
        return issues


# ---------------------------------------------------------------------------
# Логгер промптов/ответов
# ---------------------------------------------------------------------------

class PromptLogger:
    """Ведёт лог промптов и ответов для восстановления сессии."""

    def __init__(self, log_path: str):
        self.log_path = os.path.abspath(log_path)
        self._entries: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        """Загрузить существующий лог."""
        if os.path.isfile(self.log_path):
            try:
                with open(self.log_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._entries = data.get("entries", [])
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Не удалось загрузить %s: %s", self.log_path, exc)
                self._entries = []

    def log_prompt(self, content: str, file_context: str = "") -> None:
        """Записать промпт."""
        self._log("prompt", content, file_context)

    def log_response(self, content: str, file_context: str = "") -> None:
        """Записать ответ."""
        self._log("response", content, file_context)

    def _log(self, direction: str, content: str, file_context: str) -> None:
        """Внутренний метод логирования."""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        preview = content[:200].replace("\n", " ").strip()
        tokens_estimate = max(1, len(content) // 3)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "content_hash": content_hash,
            "content_preview": preview,
            "file_context": file_context,
            "tokens_estimate": tokens_estimate,
        }
        self._entries.append(entry)

    def add_decision(self, description: str, rationale: str) -> None:
        """Зафиксировать архитектурное решение."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": "decision",
            "description": description,
            "rationale": rationale,
        }
        self._entries.append(entry)

    def save(self) -> None:
        """Сохранить лог на диск."""
        data = {
            "version": VERSION,
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "total_entries": len(self._entries),
            "entries": self._entries,
        }
        try:
            with open(self.log_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except (OSError, PermissionError) as exc:
            log.error("Не удалось сохранить %s: %s", self.log_path, exc)

    def get_session_state(self) -> SessionState:
        """Получить текущее состояние сессии."""
        prompts = [e for e in self._entries if e["direction"] == "prompt"]
        responses = [e for e in self._entries if e["direction"] == "response"]
        decisions = [e for e in self._entries if e["direction"] == "decision"]
        files = set()
        for entry in self._entries:
            ctx = entry.get("file_context", "")
            if ctx:
                files.add(ctx)

        return SessionState(
            session_id=hashlib.sha256(
                (self.log_path + str(self._entries)).encode()
            ).hexdigest()[:12],
            started_at=self._entries[0]["timestamp"] if self._entries else "",
            last_activity=self._entries[-1]["timestamp"] if self._entries else "",
            files_touched=sorted(files),
            decisions=decisions,
            prompt_count=len(prompts),
            response_count=len(responses),
        )

    def get_entries(self) -> List[Dict[str, Any]]:
        """Вернуть все записи."""
        return list(self._entries)


# ---------------------------------------------------------------------------
# Генератор отчёта
# ---------------------------------------------------------------------------

class ReportBuilder:
    """Формирует итоговый отчёт."""

    def __init__(
        self,
        scan: ProjectScan,
        validation: Optional[ValidationReport] = None,
        session: Optional[SessionState] = None,
        context_preview: str = "",
        context_size: int = 0,
        elapsed_seconds: float = 0.0,
    ):
        self.scan = scan
        self.validation = validation
        self.session = session
        self.context_preview = context_preview
        self.context_size = context_size
        self.elapsed_seconds = elapsed_seconds

    def build(self) -> str:
        """Сгенерировать markdown-отчёт."""
        sections: List[str] = []

        sections.append(self._header())
        sections.append(self._workflow_metrics())
        sections.append(self._scan_report())

        if self.validation:
            sections.append(self._validation_report())

        if self.session:
            sections.append(self._session_report())

        sections.append(self._recommendations())

        if self.context_preview:
            preview = self.context_preview[:3000]
            if len(self.context_preview) > 3000:
                preview += "\n\n> ... (обрезано, полный контекст в context.md)"
            sections.append(f"## Context.md Preview\n\n```markdown\n{preview}\n```")

        return "\n\n".join(sections)

    def _header(self) -> str:
        return (
            f"# AI Workflow Optimization Report\n\n"
            f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"**Tool version:** {VERSION}"
        )

    def _workflow_metrics(self) -> str:
        """Блок метрик воркфлоу."""
        errors = 0
        warnings = 0
        infos = 0
        if self.validation:
            errors = len([i for i in self.validation.issues if i.severity == "error"])
            warnings = len([i for i in self.validation.issues if i.severity == "warning"])
            infos = len([i for i in self.validation.issues if i.severity == "info"])

        tokens_est = estimate_tokens(self.context_preview) if self.context_preview else 0

        lines = [
            "## Workflow Metrics\n",
            f"⏱ **Время выполнения:** {self.elapsed_seconds:.1f} сек",
            f"📁 **Файлов просканировано:** {self.scan.total_files}",
            f"📊 **Строк кода:** {self.scan.total_lines:,}",
        ]
        if self.validation:
            lines.append(
                f"🔍 **Найдено:** {errors} ошибок, {warnings} предупреждений, {infos} info"
            )
        lines.extend([
            f"📏 **Размер context.md:** {self.context_size:,} байт (~{tokens_est:,} токенов*)",
            f"\n> \\* оценка: 1 токен ≈ 4 байта UTF-8",
        ])
        return "\n".join(lines)

    def _scan_report(self) -> str:
        lines = [
            "## Project Scan Summary\n",
            f"- **Root:** `{self.scan.root}`",
            f"- **Total files:** {self.scan.total_files}",
            f"- **Total lines of code:** {self.scan.total_lines:,}",
            f"- **Total size:** {self.scan.total_size / 1024:.1f} KB",
            f"- **Scan time:** {self.scan.scan_time_ms:.0f} ms",
            "",
            "### File Types\n",
        ]
        for ext, count in sorted(
            self.scan.files_by_extension.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            lines.append(f"- `{ext or '(no ext)'}`: {count}")

        lines.append(f"\n### Key Files ({len(self.scan.key_files)})\n")
        for kf in self.scan.key_files:
            lines.append(
                f"- `{kf.path}` — {kf.line_count} lines, "
                f"{kf.size} bytes"
            )

        return "\n".join(lines)

    def _validation_report(self) -> str:
        if not self.validation:
            return ""

        v = self.validation
        errors = [i for i in v.issues if i.severity == "error"]
        warnings = [i for i in v.issues if i.severity == "warning"]
        infos = [i for i in v.issues if i.severity == "info"]

        lines = [
            "## Code Validation\n",
            f"- **Files checked:** {v.files_checked}",
            f"- **Check time:** {v.check_time_ms:.0f} ms",
            f"- **🔴 Errors:** {len(errors)}",
            f"- **🟡 Warnings:** {len(warnings)}",
            f"- **ℹ️  Info:** {len(infos)}",
            "",
        ]

        if errors:
            lines.append("### Errors\n")
            for issue in errors:
                lines.append(
                    f"- 🔴 `{issue.file}:{issue.line}` — [{issue.rule}] {issue.message}"
                )
            lines.append("")

        if warnings:
            lines.append("### Warnings\n")
            for issue in warnings:
                lines.append(
                    f"- 🟡 `{issue.file}:{issue.line}` — [{issue.rule}] {issue.message}"
                )
            lines.append("")

        if infos:
            lines.append("### Info\n")
            for issue in infos[:20]:
                lines.append(
                    f"- ℹ️  `{issue.file}:{issue.line}` — [{issue.rule}] {issue.message}"
                )
            if len(infos) > 20:
                lines.append(f"\n> ... ещё {len(infos) - 20} info-сообщений")
            lines.append("")

        return "\n".join(lines)

    def _session_report(self) -> str:
        if not self.session:
            return ""

        s = self.session
        lines = [
            "## Session State\n",
            f"- **Session ID:** `{s.session_id}`",
            f"- **Started:** {s.started_at or 'N/A'}",
            f"- **Last activity:** {s.last_activity or 'N/A'}",
            f"- **Prompts:** {s.prompt_count}",
            f"- **Responses:** {s.response_count}",
            f"- **Files touched:** {len(s.files_touched)}",
            f"- **Decisions recorded:** {len(s.decisions)}",
            "",
        ]

        if s.files_touched:
            lines.append("### Files Touched\n")
            for f in s.files_touched:
                lines.append(f"- `{f}`")
            lines.append("")

        if s.decisions:
            lines.append("### Decisions\n")
            for d in s.decisions:
                lines.append(
                    f"- **{d.get('description', 'N/A')}**: "
                    f"{d.get('rationale', 'N/A')}"
                )
            lines.append("")

        return "\n".join(lines)

    def _recommendations(self) -> str:
        """Сгенерировать рекомендации."""
        recs: List[str] = []

        if self.validation:
            errors = [i for i in self.validation.issues if i.severity == "error"]
            warnings = [i for i in self.validation.issues if i.severity == "warning"]

            if errors:
                recs.append(
                    "### 🔴 Критично\n"
                    f"Найдено **{len(errors)} ошибок**, требующих немедленного исправления. "
                    "Особое внимание: hardcoded secrets и syntax errors."
                )

            if warnings:
                recs.append(
                    "### 🟡 Рекомендации\n"
                    f"Найдено **{len(warnings)} предупреждений**. "
                    "Рекомендую исправить пустые catch/except в первую очередь."
                )

            if self.scan.total_lines > 10000:
                recs.append(
                    "### 📦 Оптимизация контекста\n"
                    f"Проект содержит **{self.scan.total_lines:,} строк**. "
                    "При работе с AI используй `--compress` для экономии контекстного окна."
                )

        if not recs:
            recs.append("✅ Критических проблем не обнаружено. Проект в хорошем состоянии.")

        return "## Recommendations\n\n" + "\n\n".join(recs)


# ---------------------------------------------------------------------------
# CLI и основная логика
# ---------------------------------------------------------------------------

def build_cli() -> argparse.ArgumentParser:
    """Создать парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        prog="ai_workflow_optimizer",
        description="AI Workflow Optimizer v2 — оптимизация работы с AI-ассистентом в IDE",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры использования:
  %(prog)s                                  # Полный анализ
  %(prog)s --validate                       # Валидация кода
  %(prog)s --compress                       # Сжатый context.md
  %(prog)s --status                         # Статус сессии
  %(prog)s --diff --validate                # Только изменённые файлы
  %(prog)s --copy-context                   # + копирование в буфер обмена
  %(prog)s --dry-run                        # Пробный запуск (без записи)
  %(prog)s /path/to/project --validate      # Анализ другого проекта
  %(prog)s --ignore dist --ignore temp      # Доп. ignore-паттерны
        """,
    )

    parser.add_argument(
        "project_path",
        nargs="?",
        default=".",
        help="Путь к проекту (по умолчанию текущая директория)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Запустить валидацию кода на типовые ошибки ИИ",
    )
    parser.add_argument(
        "--compress",
        action="store_true",
        help="Сжать context.md (удалить содержимое файлов, оставить метаданные)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Показать статус сессии и статистику",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Пробный запуск: анализ без записи файлов",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="Сканировать только изменённые файлы из git diff (fallback на полный скан)",
    )
    parser.add_argument(
        "--copy-context",
        action="store_true",
        help="После генерации context.md скопировать в буфер обмена",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Дополнительный ignore-паттерн (можно указывать несколько раз)",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Путь для сохранения отчёта (по умолчанию: ai_opt_report.md)",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Не генерировать отчёт в файл",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {VERSION}",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Точка входа."""
    parser = build_cli()
    args = parser.parse_args(argv)

    # Определяем путь к скрипту (для поиска rules.json)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    rules_path = os.path.join(script_dir, DEFAULT_RULES_FILENAME)

    # Загрузка правил
    rule_loader = RuleLoader(rules_path)
    rules = rule_loader.load()
    if rule_loader.loaded_external:
        print("📜 Загружены внешние правила из rules.json")

    project_root = os.path.abspath(args.project_path)
    if not os.path.isdir(project_root):
        log.error("Директория не существует: %s", project_root)
        return 1

    can_write = check_write_permission(project_root)

    # Замер общего времени
    perf_start = time.perf_counter()

    print("=" * 60)
    print(f"  AI Workflow Optimizer v{VERSION}")
    print("=" * 60)
    print()

    # ------------------------------------------------------------------
    # 1. Сканирование (полное или --diff)
    # ------------------------------------------------------------------
    if args.diff:
        print("📦 Режим --diff: сканирование изменённых файлов из git...")
        scan, diff_ok = ProjectScanner.scan_diff(project_root, extra_ignores=args.ignore)
        if diff_ok:
            print(f"   📦 Режим --diff: просканировано {scan.total_files} изменённых файлов")
            if not scan.total_files:
                print("   Нет изменённых файлов в git. Полный скан.")
                scan = ProjectScanner(project_root, extra_ignores=args.ignore).scan()
        else:
            print("   ⚠️  Git diff недоступен — fallback на полный скан проекта")
    else:
        print(f"📂 Сканирование: {project_root}")
        scanner = ProjectScanner(project_root, extra_ignores=args.ignore)
        scan = scanner.scan()

    print(f"   Файлов: {scan.total_files}")
    print(f"   Строк кода: {scan.total_lines:,}")
    print(f"   Размер: {scan.total_size / 1024:.1f} KB")
    print(f"   Время: {scan.scan_time_ms:.0f} ms")
    if not args.diff:
        print(f"   Игнорировано директорий: {len(set(scan.ignored_dirs))}")
        print(f"   Игнорировано файлов: {len(scan.ignored_files)}")
    print()

    # ------------------------------------------------------------------
    # 2. Генерация context.md
    # ------------------------------------------------------------------
    context_path = os.path.join(project_root, "context.md")
    print(f"📝 Генерация context.md{' (сжатый)' if args.compress else ''}...")
    builder = ContextBuilder(scan, project_root)
    context_content = builder.build(compress=args.compress)

    context_written = False
    if not args.dry_run and can_write:
        try:
            with open(context_path, "w", encoding="utf-8") as f:
                f.write(context_content)
            context_written = True
            print(f"   ✅ Сохранён: {context_path} ({len(context_content):,} символов)")
        except (OSError, PermissionError) as exc:
            log.error("Не удалось записать %s: %s", context_path, exc)
    elif args.dry_run:
        print(f"   🔍 Dry-run: {len(context_content):,} символов (файл не записан)")
    else:
        print(f"   ⚠️  Нет прав на запись: {project_root}")

    # --copy-context
    if args.copy_context:
        print("📋 Копирование context.md в буфер обмена...")
        if copy_to_clipboard(context_content):
            print("   ✅ context.md скопирован в буфер обмена. Готово к вставке в AI-чат.")
        else:
            print("   ⚠️  Не удалось скопировать в буфер обмена. "
                  "Windows: убедитесь, что clip.exe доступен в PATH.")

    print()

    # ------------------------------------------------------------------
    # 3. Валидация
    # ------------------------------------------------------------------
    validation_report: Optional[ValidationReport] = None
    if args.validate:
        print("🔍 Валидация кода...")
        validator = CodeValidator(project_root, rules=rules)
        code_files = [
            f for f in scan.all_files
            if not f.is_binary and f.extension in (".py", ".cs")
        ]
        validation_report = validator.validate(code_files)

        errors = [i for i in validation_report.issues if i.severity == "error"]
        warnings = [i for i in validation_report.issues if i.severity == "warning"]
        infos = [i for i in validation_report.issues if i.severity == "info"]

        print(f"   Проверено файлов: {validation_report.files_checked}")
        print(f"   🔴 Errors: {len(errors)}")
        print(f"   🟡 Warnings: {len(warnings)}")
        print(f"   ℹ️  Info: {len(infos)}")
        print(f"   Время: {validation_report.check_time_ms:.0f} ms")

        if errors:
            print("\n   🔴 Ошибки:")
            for issue in errors[:10]:
                print(f"      {issue.file}:{issue.line} — {issue.message}")
            if len(errors) > 10:
                print(f"      ... и ещё {len(errors) - 10}")

        if warnings:
            print("\n   🟡 Предупреждения:")
            for issue in warnings[:10]:
                print(f"      {issue.file}:{issue.line} — {issue.message}")
            if len(warnings) > 10:
                print(f"      ... и ещё {len(warnings) - 10}")
        print()

    # ------------------------------------------------------------------
    # 4. Лог промптов / статус сессии
    # ------------------------------------------------------------------
    prompt_log_path = os.path.join(project_root, "prompts_log.json")
    prompt_logger = PromptLogger(prompt_log_path)
    session_state = prompt_logger.get_session_state()

    if args.status:
        print("📊 Статус сессии:")
        print(f"   Session ID: {session_state.session_id}")
        print(f"   Начата: {session_state.started_at or 'нет данных'}")
        print(f"   Последняя активность: {session_state.last_activity or 'нет данных'}")
        print(f"   Промптов: {session_state.prompt_count}")
        print(f"   Ответов: {session_state.response_count}")
        print(f"   Файлов затронуто: {len(session_state.files_touched)}")
        print(f"   Решений записано: {len(session_state.decisions)}")
        if session_state.files_touched:
            print("\n   Файлы:")
            for f in session_state.files_touched[:10]:
                print(f"      {f}")
            if len(session_state.files_touched) > 10:
                print(f"      ... и ещё {len(session_state.files_touched) - 10}")
        print()

    # ------------------------------------------------------------------
    # 5. Генерация отчёта
    # ------------------------------------------------------------------
    elapsed = time.perf_counter() - perf_start

    if not args.no_report:
        report_path = args.output or os.path.join(project_root, "ai_opt_report.md")
        print(f"📋 Генерация отчёта...")

        context_preview = context_content[:1000]

        report_builder = ReportBuilder(
            scan=scan,
            validation=validation_report,
            session=session_state,
            context_preview=context_preview,
            context_size=len(context_content.encode("utf-8")),
            elapsed_seconds=elapsed,
        )
        report_content = report_builder.build()

        if not args.dry_run and can_write:
            try:
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write(report_content)
                print(f"   ✅ Сохранён: {report_path}")
            except (OSError, PermissionError) as exc:
                log.error("Не удалось записать %s: %s", report_path, exc)
        elif args.dry_run:
            print(f"   🔍 Dry-run: {len(report_content):,} символов (файл не записан)")
        else:
            print(f"   ⚠️  Нет прав на запись: {project_root}")
        print()

    # ------------------------------------------------------------------
    # Итог
    # ------------------------------------------------------------------
    print("=" * 60)
    print(f"  Готово! ({elapsed:.1f} сек)")
    if args.dry_run:
        print("  (Dry-run — файлы не записаны)")
    print("=" * 60)

    if validation_report:
        errors = [i for i in validation_report.issues if i.severity == "error"]
        if errors:
            return 2
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
