#!/usr/bin/env python3
"""Сборка мини-репозиториев из спецификаций в кэш.

    python3 build.py [--rebuild] [name ...]

Репозиторий кладётся в `~/.cache/zodchiy-evals/<name>-<hash спецификации>`.
Хэш в имени — чтобы правка спецификации не переиспользовала старый репозиторий
молча: это ровно тот класс ошибки, из-за которого зелёный прогон ничего не
значит.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import specs  # noqa: E402

CACHE = os.path.expanduser("~/.cache/zodchiy-evals")

AUTHORS = [("Автор Один", "one@example.test"), ("Автор Два", "two@example.test")]

# Окно behavior.py по умолчанию — 18 месяцев. Историю укладываем в последние
# 300 дней: иначе часть коммитов выпадет из окна и числа поедут без причины.
WINDOW_DAYS = 300
TAIL_DAYS = 20


def spec_hash(sp: dict) -> str:
    blob = json.dumps(sp, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()[:10]


def repo_path(name: str) -> str:
    return os.path.join(CACHE, f"{name}-{spec_hash(specs.spec(name))}")


def _git(repo: str, *args: str, env: dict | None = None) -> None:
    subprocess.run(
        ["git", "-C", repo, *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


# Метка достроенности. Наличия .git недостаточно: прерванная сборка оставляет
# каталог, который выглядит кэшем и молча отдаёт огрызок истории — а прогон на
# нём зелёный ровно там, где нечего мерить. Поймано на себе.
STAMP = ".zodchiy-fixture-complete"


def build(name: str, rebuild: bool = False) -> str:
    sp = specs.spec(name)
    path = repo_path(name)
    stamp = os.path.join(path, STAMP)
    if os.path.exists(stamp) and not rebuild:
        with open(stamp, encoding="utf-8") as fh:
            done = json.load(fh)
        if done.get("commits") == len(sp["commits"]):
            return path
    if os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path)

    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", path],
        check=True,
        capture_output=True,
    )
    _git(path, "config", "user.name", AUTHORS[0][0])
    _git(path, "config", "user.email", AUTHORS[0][1])
    _git(path, "config", "commit.gpgsign", "false")

    commits = sp["commits"]
    now = int(time.time())
    start = now - WINDOW_DAYS * 86400
    step = max(1, ((WINDOW_DAYS - TAIL_DAYS) * 86400) // max(1, len(commits)))

    when = start
    for i, c in enumerate(commits):
        for rel in c["files"]:
            full = os.path.join(path, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            if c.get("create"):
                with open(full, "w", encoding="utf-8") as fh:
                    fh.write(sp["files"][rel])
            else:
                # Дописываем комментарий: правка настоящая для git и нейтральная
                # для разбора — ни импорты, ни цикломатическая от неё не едут.
                with open(full, "a", encoding="utf-8") as fh:
                    fh.write(f"# rev {i}\n")
        # gap_days задаёт разрыв явно: пороги эпизода (<=3 дней) и окна доделки
        # (<=14 дней) иначе не проверить — равномерный шаг попадает мимо обоих.
        when = when + int(c["gap_days"] * 86400) if "gap_days" in c else start + step * i
        author = AUTHORS[i % len(AUTHORS)] if name == "churn-pain-vs-extension" else AUTHORS[0]
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": author[0],
            "GIT_AUTHOR_EMAIL": author[1],
            "GIT_COMMITTER_NAME": author[0],
            "GIT_COMMITTER_EMAIL": author[1],
            "GIT_AUTHOR_DATE": f"{when} +0000",
            "GIT_COMMITTER_DATE": f"{when} +0000",
        }
        _git(path, "add", "-A", env=env)
        _git(path, "commit", "-q", "-m", c["subject"], env=env)

    actual = int(
        subprocess.run(
            ["git", "-C", path, "rev-list", "--count", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    )
    if actual != len(sp["commits"]):
        raise RuntimeError(f"{name}: собрано {actual} коммитов из {len(sp['commits'])}")
    with open(os.path.join(path, STAMP), "w", encoding="utf-8") as fh:
        json.dump({"name": name, "commits": actual}, fh)
    return path


def main():
    ap = argparse.ArgumentParser(description="Сборка фикстур zodchiy")
    ap.add_argument("names", nargs="*", default=None)
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    names = a.names or specs.all_names()
    out = {}
    for n in names:
        out[n] = build(n, rebuild=a.rebuild)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
