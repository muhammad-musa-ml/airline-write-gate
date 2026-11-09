import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from airline_write_gate.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
AGENT = "fixture-from-gpt-4.1-2025-04-14"


@pytest.fixture
def data_dir(tmp_path):
    shutil.copy(FIXTURES / "runs.json", tmp_path / "fixture_airline_default_x_4trials.json")
    shutil.copy(FIXTURES / "later_tasks.json", tmp_path / "tasks.json")
    return tmp_path


@pytest.fixture
def splits(tmp_path):
    path = tmp_path / "splits.json"
    path.write_text(json.dumps({"development": [AGENT], "held_out": []}), encoding="utf-8")
    return path


def run_eval(data_dir, splits, out, *flags):
    return main(["eval", "--data", str(data_dir), "--splits", str(splits), "--out", str(out), *flags])


def test_eval_write_then_check_passes(data_dir, splits, tmp_path, capsys):
    out = tmp_path / "results"
    assert run_eval(data_dir, splits, out, "--write") == 0
    assert (out / "results.json").exists() and (out / "results.md").exists()
    assert run_eval(data_dir, splits, out, "--check") == 0
    assert "results match" in capsys.readouterr().out


def test_eval_check_fails_when_a_committed_number_is_wrong(data_dir, splits, tmp_path, capsys):
    out = tmp_path / "results"
    run_eval(data_dir, splits, out, "--write")
    path = out / "results.json"
    path.write_text(path.read_text(encoding="utf-8").replace('"writes_ran": 5', '"writes_ran": 6'), encoding="utf-8")
    assert run_eval(data_dir, splits, out, "--check") == 1
    assert "results.json" in capsys.readouterr().err


def test_eval_check_fails_when_the_results_are_missing(data_dir, splits, tmp_path):
    assert run_eval(data_dir, splits, tmp_path / "nothing", "--check") == 1


def test_eval_without_the_later_tasks_file_is_a_usage_error(data_dir, splits, tmp_path, capsys):
    (data_dir / "tasks.json").unlink()
    assert run_eval(data_dir, splits, tmp_path / "r", "--write") == 2
    assert "fetch_data.py" in capsys.readouterr().err


def test_a_split_that_names_a_missing_model_is_a_usage_error(data_dir, tmp_path, capsys):
    bad = tmp_path / "bad-splits.json"
    bad.write_text(json.dumps({"development": ["no-such-model"], "held_out": []}), encoding="utf-8")
    assert run_eval(data_dir, bad, tmp_path / "r", "--write") == 2
    assert "no-such-model" in capsys.readouterr().err


def test_an_empty_data_folder_says_to_fetch(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        main(["eval", "--data", str(tmp_path)])
    assert "fetch_data.py" in str(stopped.value)


def test_replay_prints_each_write_with_its_rule_and_repair(capsys):
    code = main(["replay", str(FIXTURES / "runs.json"), "--task", "37", "--trial", "1"])
    out = capsys.readouterr().out
    assert code == 0
    assert "BLOCK cancel_reservation (ran)" in out
    assert "cancel-eligibility [block]" in out
    assert "repair:" in out


def test_replay_of_a_run_that_is_not_there_is_a_usage_error(capsys):
    assert main(["replay", str(FIXTURES / "runs.json"), "--task", "99", "--trial", "0"]) == 2
    assert "no task 99" in capsys.readouterr().err


def test_a_missing_file_is_a_usage_error_not_a_traceback(tmp_path, capsys):
    assert main(["replay", str(tmp_path / "nope.json"), "--task", "1", "--trial", "0"]) == 2
    assert "error:" in capsys.readouterr().err


def test_bench_prints_a_percentile_line_per_pass(data_dir, capsys):
    assert main(["bench", "--data", str(data_dir), "--runs", "2"]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("pass ")]
    assert len(lines) == 2 and "p99" in lines[0]


def load_fetch_module():
    spec = importlib.util.spec_from_file_location("fetch_data", ROOT / "scripts" / "fetch_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_blob_hash_is_git_blob_hash():
    # git hash-object of a file holding "hello" and a newline
    assert load_fetch_module().blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_every_pinned_file_has_a_40_character_hash():
    files = load_fetch_module().FILES
    assert len(files) == 6
    assert all(len(sha) == 40 and set(sha) <= set("0123456789abcdef") for sha in files.values())
