from __future__ import annotations

from main import _resolve_task_id


def test_resolve_task_id_exact_match() -> None:
    tasks = {"abc123xxxx": object(), "def456yyyy": object()}
    resolved, err = _resolve_task_id("abc123xxxx", tasks)
    assert resolved == "abc123xxxx"
    assert err is None


def test_resolve_task_id_prefix_unique_match() -> None:
    tasks = {"abc123xxxx": object(), "def456yyyy": object()}
    resolved, err = _resolve_task_id("abc123", tasks)
    assert resolved == "abc123xxxx"
    assert err is None


def test_resolve_task_id_prefix_ambiguous() -> None:
    tasks = {"abc123xxxx": object(), "abc123zzzz": object()}
    resolved, err = _resolve_task_id("abc123", tasks)
    assert resolved is None
    assert err is not None and "ambiguous" in err
