"""Tests for the individual tools: read_file, search_replace, bash, grep, list_dir."""
import os
import tempfile
import pytest
from src.core.tools.read_file import read_file
from src.core.tools.search_replace import search_replace
from src.core.tools.bash import run_bash, BashInput
from src.core.tools.grep import grep, GrepInput
from src.core.tools.list_dir import list_dir, ListDirInput


class TestReadFile:
    def test_read_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\nline4\nline5\n")
        result = read_file(str(tmp_path), path=str(f), offset=1, limit=3)
        assert result.success is True
        assert result.content.total_lines == 6  # trailing newline adds a line
        assert "1→line1" in result.content.content
        assert "line3" in result.content.content
        assert "line4" not in result.content.content

    def test_read_nonexistent_file(self, tmp_path):
        result = read_file(str(tmp_path), path="nonexistent.txt")
        assert result.success is False
        assert result.error is not None

    def test_line_number_format(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("import os\nimport sys\n")
        result = read_file(str(tmp_path), path=str(f))
        # Only first line and every 10th line get line number prefix
        assert "1→import os" in result.content.content
        assert "import sys" in result.content.content


class TestSearchReplace:
    def test_exact_match(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello world\nfoo bar\n")
        result = search_replace(str(f), "hello", "goodbye", str(tmp_path))
        assert result.success is True
        assert len(result.edits) == 1
        assert f.read_text() == "goodbye world\nfoo bar\n"

    def test_no_match(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello world\n")
        result = search_replace(str(f), "zzz_not_found_zzz", "x", str(tmp_path))
        assert result.success is False
        assert result.error_type == "NoMatchesFound"

    def test_multiple_matches_no_replace_all(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello hello\n")
        result = search_replace(str(f), "hello", "x", str(tmp_path), replace_all=False)
        assert result.success is False
        assert result.error_type == "MultipleMatchesFound"

    def test_replace_all(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello world hello\n")
        result = search_replace(str(f), "hello", "X", str(tmp_path), replace_all=True)
        assert result.success is True
        assert f.read_text() == "X world X\n"

    def test_create_file_with_empty_old_string(self, tmp_path):
        new_file = tmp_path / "newfile.txt"
        result = search_replace(str(new_file), "", "new content", str(tmp_path))
        assert result.success is True
        assert new_file.read_text() == "new content"


class TestBash:
    def test_simple_command(self, tmp_path):
        result = run_bash(BashInput(command="echo hello_test"), str(tmp_path))
        assert result.success is True
        assert result.exit_code == 0
        assert "hello_test" in result.output

    def test_command_with_description(self, tmp_path):
        result = run_bash(
            BashInput(command="echo done", description="Test echo"),
            str(tmp_path),
        )
        assert "done" in result.output

    def test_failed_command(self, tmp_path):
        result = run_bash(BashInput(command="exit 1"), str(tmp_path))
        assert result.success is False
        assert result.exit_code == 1


class TestGrep:
    def test_basic_pattern(self, tmp_path):
        f = tmp_path / "src.py"
        f.write_text("def foo():\n    pass\ndef bar():\n    pass\n")
        result = grep(GrepInput(pattern="def.*\\(\\):"), str(tmp_path))
        assert result.success is True
        assert len(result.matches) == 2

    def test_no_matches(self, tmp_path):
        f = tmp_path / "src.py"
        f.write_text("hello world\n")
        result = grep(GrepInput(pattern="zzz_nonexistent_zzz"), str(tmp_path))
        assert result.success is True
        assert len(result.matches) == 0

    def test_case_insensitive(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("HELLO world\nhello again\n")
        result = grep(GrepInput(pattern="hello", case_insensitive=True), str(tmp_path))
        assert len(result.matches) == 2


class TestListDir:
    def test_list_directory(self, tmp_path):
        (tmp_path / "a.py").write_text("1")
        (tmp_path / "b.txt").write_text("2")
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "c.py").write_text("3")

        result = list_dir(ListDirInput(path=str(tmp_path), depth=2), str(tmp_path))
        assert result.success is True
        assert "a.py" in result.output
        assert "b.txt" in result.output
        assert "sub" in result.output or "sub/" in result.output

    def test_default_path_is_workspace(self, tmp_path):
        (tmp_path / "only.txt").write_text("x")
        result = list_dir(ListDirInput(path=None, depth=1), str(tmp_path))
        assert result.success is True
        assert "only.txt" in result.output
