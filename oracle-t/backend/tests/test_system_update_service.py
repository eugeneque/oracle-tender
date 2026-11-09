"""Обновление из интерфейса (раздел 86 ARCHITECTURE.md): разбор журнала изменений, вывода
консоли и файлов состояния, которые пишет «update.sh --web»."""

from pathlib import Path

import pytest

from app.services import system_update_service as svc

LOCAL = '''
export const CHANGELOG: Release[] = [
  {
    date: "2026-11-08",
    title: "Старое",
    items: [
      { kind: "new", text: "Пункт А" },
    ],
  },
];
'''

REMOTE = '''
export const CHANGELOG: Release[] = [
  {
    date: "2026-11-09",
    title: "Новое «в кавычках»",
    items: [
      {
        kind: "fixed",
        text: "Текст с \\"экранированными\\" кавычками",
      },
    ],
  },
  {
    date: "2026-11-08",
    title: "Старое",
    items: [
      { kind: "new", text: "Пункт А" },
      { kind: "improved", text: "Пункт Б — дописан позже" },
    ],
  },
];
'''


def test_new_releases_only_missing_entries_and_items():
    result = svc.new_releases(LOCAL, REMOTE)
    assert [r["title"] for r in result] == ["Новое «в кавычках»", "Старое"]
    assert result[0]["items"] == [{"kind": "fixed", "text": 'Текст с "экранированными" кавычками'}]
    # Запись той же даты и заголовка — только дописанные пункты.
    assert result[1]["items"] == [{"kind": "improved", "text": "Пункт Б — дописан позже"}]


def test_new_releases_same_versions_empty():
    assert svc.new_releases(REMOTE, REMOTE) == []


def test_real_changelog_parses():
    source = (Path(__file__).resolve().parents[2] / "frontend/src/content/changelog.ts").read_text(encoding="utf-8")
    releases = svc.parse_changelog(source)
    assert len(releases) > 10
    assert all(r["date"] and r["title"] for r in releases)
    assert sum(len(r["items"]) for r in releases) >= len(releases)


def test_clean_console_strips_colors_and_redraws():
    raw = "\x1b[32m✔\x1b[0m готово\n\r⠋ сборка\r   ... сборка\n"
    assert svc.clean_console(raw) == "✔ готово\n   ... сборка"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "root_dir", lambda: tmp_path)
    monkeypatch.setattr(svc, "_systemctl", lambda *args: None)
    return tmp_path


def test_run_info_idle_without_files(root):
    info = svc.run_info()
    assert info["state"] == "idle"
    assert info["log"] == ""


def test_run_info_reads_state_and_log(root):
    (root / "update-web.state").write_text("state=failed\ncode=3\nresult=error\nby=admin\n")
    (root / "update-web.log").write_text("\x1b[31m✖ ошибка\x1b[0m\n")
    info = svc.run_info()
    assert info["state"] == "failed"
    assert info["code"] == 3
    assert info["requested_by"] == "admin"
    assert info["log"] == "✖ ошибка"


def test_request_file_means_queued_and_cancel_removes_it(root):
    (root / ".update-request").write_text("user=admin\nforce=0\n")
    assert svc.run_info()["state"] == "queued"
    assert svc.cancel_request()["state"] == "idle"
    assert not (root / ".update-request").exists()


def test_request_update_refused_outside_server(root):
    (root / "update.sh").write_text("#!/bin/sh\n")
    with pytest.raises(svc.UpdateError):
        svc.request_update(username="admin", force=False)
    assert not (root / ".update-request").exists()


def test_running_state_without_live_service_is_interrupted(root, monkeypatch):
    (root / "update-web.state").write_text("state=running\n")
    monkeypatch.setattr(svc, "_systemctl", lambda *args: "inactive")
    assert svc.run_info()["state"] == "interrupted"
