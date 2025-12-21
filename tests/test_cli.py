"""Tests for tsync CLI."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tsync.cli import (
    Config,
    findup,
    find_files,
    get_file_hashes,
    resolve_remote,
    parse_args,
)


class TestFindup:
    """Tests for findup function."""

    def test_finds_file_in_current_dir(self, tmp_path: Path) -> None:
        """Find target file in the starting directory."""
        (tmp_path / "target.yaml").touch()
        result = findup("target.yaml", tmp_path)
        assert result == tmp_path

    def test_finds_file_in_parent_dir(self, tmp_path: Path) -> None:
        """
        Find target file by traversing upward.

        Directory structure:
            tmp_path/
            ├── target.yaml
            └── sub/
                └── deep/      <- start here
        """
        (tmp_path / "target.yaml").touch()
        subdir = tmp_path / "sub" / "deep"
        subdir.mkdir(parents=True)
        result = findup("target.yaml", subdir)
        assert result == tmp_path

    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        """Return None when file doesn't exist in any parent."""
        result = findup("nonexistent.yaml", tmp_path)
        assert result is None

    def test_stops_at_root(self, tmp_path: Path) -> None:
        """
        Return None when reaching filesystem root without finding file.

        Directory structure:
            tmp_path/
            └── a/
                └── b/
                    └── c/     <- start here, no target.yaml anywhere
        """
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        result = findup("nonexistent.yaml", deep)
        assert result is None


class TestFindFiles:
    """Tests for find_files function."""

    def test_finds_all_files(self, tmp_path: Path) -> None:
        """
        Find all files recursively.

        Directory structure:
            tmp_path/
            ├── a.txt
            ├── b.txt
            └── sub/
                └── c.txt
        """
        (tmp_path / "a.txt").touch()
        (tmp_path / "b.txt").touch()
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "c.txt").touch()

        files = find_files(str(tmp_path))
        assert len(files) == 3

    def test_ignores_git_by_default(self, tmp_path: Path) -> None:
        """
        Ignore .git directory by default.

        Directory structure:
            tmp_path/
            ├── a.txt          <- found
            └── .git/
                └── config     <- ignored
        """
        (tmp_path / "a.txt").touch()
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").touch()

        files = find_files(str(tmp_path))
        filenames: list[str] = [f.name for f in files]
        assert "a.txt" in filenames
        assert "config" not in filenames

    def test_ignores_specified_dirs(self, tmp_path: Path) -> None:
        """
        Ignore custom directories.

        Directory structure:
            tmp_path/
            ├── a.txt              <- found
            └── __pycache__/
                └── module.pyc     <- ignored
        """
        (tmp_path / "a.txt").touch()
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "module.pyc").touch()

        files = find_files(str(tmp_path), ignore_dirs=['.git', '__pycache__'])
        filenames: list[str] = [f.name for f in files]
        assert "a.txt" in filenames
        assert "module.pyc" not in filenames

    def test_ignores_specified_files(self, tmp_path: Path) -> None:
        """Ignore files by name."""
        (tmp_path / "keep.txt").touch()
        (tmp_path / "ignore.txt").touch()

        files = find_files(str(tmp_path), ignore_files=['ignore.txt'], ignore_dirs=[])
        filenames: list[str] = [f.name for f in files]
        assert "keep.txt" in filenames
        assert "ignore.txt" not in filenames


class TestGetFileHashes:
    """Tests for get_file_hashes function."""

    def test_computes_hashes(self, tmp_path: Path) -> None:
        """Compute MD5 hashes for files."""
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world")

        hashes: dict[str, str] = get_file_hashes(str(tmp_path))
        assert len(hashes) == 2
        assert "a.txt" in hashes
        assert "b.txt" in hashes
        # MD5 of "hello"
        assert hashes["a.txt"] == "5d41402abc4b2a76b9719d911017c592"

    def test_relative_paths(self, tmp_path: Path) -> None:
        """
        Use relative paths as keys in hash dict.

        Directory structure:
            tmp_path/
            └── sub/
                └── file.txt

        Hash key should be "sub/file.txt", not absolute path.
        """
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "file.txt").write_text("test")

        hashes: dict[str, str] = get_file_hashes(str(tmp_path))
        assert "sub/file.txt" in hashes


class TestResolveRemote:
    """Tests for resolve_remote function."""

    def test_passthrough_ssh_remote(self) -> None:
        """Pass through user@host:/path format."""
        assert resolve_remote("user@host:/path") == "user@host:/path"

    def test_passthrough_local_path(self) -> None:
        """Pass through absolute local paths."""
        assert resolve_remote("/local/path") == "/local/path"

    def test_passthrough_ssh_config_host(self) -> None:
        """Pass through hostname:/path format (ssh config alias)."""
        assert resolve_remote("hostname:/path") == "hostname:/path"


class TestConfig:
    """Tests for Config dataclass."""

    def test_default_values(self) -> None:
        """Verify default field values."""
        config = Config()
        assert config.remotes == {}
        assert config.files == []
        assert config.excludes == []
        assert config.includes == []
        assert config.dry_run is False

    def test_update_from_dict(self) -> None:
        """Update config from dictionary."""
        config = Config()
        config.update({
            'remotes': {'server': 'host:/path'},
            'excludes': ['*.pyc'],
            'dry_run': True,
        })
        assert config.remotes == {'server': 'host:/path'}
        assert config.excludes == ['*.pyc']
        assert config.dry_run is True

    def test_update_ignores_missing_keys(self) -> None:
        """Ignore keys not in dataclass fields."""
        config = Config()
        config.update({'nonexistent': 'value'})
        # Should not raise, just ignore


class TestParseArgs:
    """Tests for argument parsing."""

    def test_push_with_targets(self) -> None:
        """Parse push command with multiple targets."""
        with patch('sys.argv', ['tsync', 'push', 'server1', 'server2']):
            args, _ = parse_args()
            assert args.mode == 'push'
            assert args.target == ['server1', 'server2']

    def test_pull_with_source(self) -> None:
        """Parse pull command with source."""
        with patch('sys.argv', ['tsync', 'pull', 'server']):
            args, _ = parse_args()
            assert args.mode == 'pull'
            assert args.source == 'server'

    def test_diff_mode(self) -> None:
        """Parse diff command."""
        with patch('sys.argv', ['tsync', 'diff', 'server']):
            args, _ = parse_args()
            assert args.mode == 'diff'
            assert args.remote == 'server'

    def test_global_options(self) -> None:
        """Parse global options before subcommand."""
        with patch('sys.argv', ['tsync', '-y', '-d', 'push', 'server']):
            args, _ = parse_args()
            assert args.no_confirm is True
            assert args.dry_run is True

    def test_extra_args_passthrough(self) -> None:
        """Pass unrecognized args through for rsync."""
        with patch('sys.argv', ['tsync', 'push', 'server', '--bwlimit=1000']):
            args, extra = parse_args()
            assert '--bwlimit=1000' in extra


# Integration tests require rsync and actual filesystem operations
# Run with: pytest tests/ -m integration


@pytest.mark.integration
class TestIntegration:
    """Integration tests using local paths only (no SSH)."""

    def test_push_to_local_path(self, tmp_path: Path) -> None:
        """
        Test pushing current directory to a local backup path.

        Directory structure before:
            tmp_path/
            ├── project/
            │   ├── .tsync.yaml
            │   ├── file.txt
            │   └── subdir/
            │       └── nested.txt
            └── backup/

        Directory structure after push:
            tmp_path/
            ├── project/
            │   └── ...
            └── backup/
                ├── file.txt
                └── subdir/
                    └── nested.txt
        """
        project = tmp_path / "project"
        project.mkdir()
        (project / "file.txt").write_text("hello")
        subdir = project / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("nested content")

        backup = tmp_path / "backup"
        backup.mkdir()

        config = project / ".tsync.yaml"
        config.write_text(f"remotes:\n  backup: {backup}\n")

        # Run the CLI
        result = subprocess.run(
            [sys.executable, "-m", "tsync.cli", "push", "backup", "-y"],
            cwd=project,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Push failed: {result.stderr}"
        assert (backup / "file.txt").exists()
        assert (backup / "file.txt").read_text() == "hello"
        assert (backup / "subdir" / "nested.txt").exists()
        assert (backup / "subdir" / "nested.txt").read_text() == "nested content"

    def test_pull_from_local_path(self, tmp_path: Path) -> None:
        """
        Test pulling from a local backup path.

        Directory structure before:
            tmp_path/
            ├── project/
            │   └── .tsync.yaml
            └── backup/
                ├── file.txt
                └── subdir/
                    └── nested.txt

        Directory structure after pull:
            tmp_path/
            ├── project/
            │   ├── .tsync.yaml
            │   ├── file.txt
            │   └── subdir/
            │       └── nested.txt
            └── backup/
                └── ...
        """
        project = tmp_path / "project"
        project.mkdir()

        backup = tmp_path / "backup"
        backup.mkdir()
        (backup / "file.txt").write_text("from backup")
        subdir = backup / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_text("nested from backup")

        config = project / ".tsync.yaml"
        config.write_text(f"remotes:\n  backup: {backup}\n")

        result = subprocess.run(
            [sys.executable, "-m", "tsync.cli", "pull", "backup", "-y"],
            cwd=project,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Pull failed: {result.stderr}"
        assert (project / "file.txt").exists()
        assert (project / "file.txt").read_text() == "from backup"
        assert (project / "subdir" / "nested.txt").exists()

    def test_push_respects_excludes(self, tmp_path: Path) -> None:
        """
        Test that excludes in config are respected.

        Directory structure:
            tmp_path/
            ├── project/
            │   ├── .tsync.yaml
            │   ├── keep.txt        <- synced
            │   └── __pycache__/
            │       └── cache.pyc   <- excluded
            └── backup/
        """
        project = tmp_path / "project"
        project.mkdir()
        (project / "keep.txt").write_text("keep me")
        cache = project / "__pycache__"
        cache.mkdir()
        (cache / "cache.pyc").write_text("ignore me")

        backup = tmp_path / "backup"
        backup.mkdir()

        config = project / ".tsync.yaml"
        config.write_text(
            f"remotes:\n  backup: {backup}\nexcludes:\n  - __pycache__\n"
        )

        result = subprocess.run(
            [sys.executable, "-m", "tsync.cli", "push", "backup", "-y"],
            cwd=project,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert (backup / "keep.txt").exists()
        assert not (backup / "__pycache__").exists()

    def test_push_from_subdirectory(self, tmp_path: Path) -> None:
        """
        Test pushing from a subdirectory preserves relative path.

        Directory structure:
            tmp_path/
            ├── project/
            │   ├── .tsync.yaml      <- config at root
            │   └── src/
            │       └── module.py      <- push from here
            └── backup/

        After push from src/:
            tmp_path/
            └── backup/
                └── src/
                    └── module.py      <- preserves src/ prefix
        """
        project = tmp_path / "project"
        project.mkdir()
        src = project / "src"
        src.mkdir()
        (src / "module.py").write_text("print('hello')")

        backup = tmp_path / "backup"
        backup.mkdir()

        config = project / ".tsync.yaml"
        config.write_text(f"remotes:\n  backup: {backup}\n")

        # Push from subdirectory
        result = subprocess.run(
            [sys.executable, "-m", "tsync.cli", "push", "backup", "-y"],
            cwd=src,  # Run from src/ subdirectory
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Push failed: {result.stderr}"
        # File should be at backup/src/module.py, preserving the relative path
        assert (backup / "src" / "module.py").exists()
        assert (backup / "src" / "module.py").read_text() == "print('hello')"

    def test_dry_run_does_not_sync(self, tmp_path: Path) -> None:
        """Test that --dry-run shows commands but doesn't sync files."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "file.txt").write_text("content")

        backup = tmp_path / "backup"
        backup.mkdir()

        config = project / ".tsync.yaml"
        config.write_text(f"remotes:\n  backup: {backup}\n")

        result = subprocess.run(
            [sys.executable, "-m", "tsync.cli", "push", "backup", "-y", "-d"],
            cwd=project,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        # File should NOT exist because it was a dry run
        assert not (backup / "file.txt").exists()

    def test_push_specific_file_with_f_flag(self, tmp_path: Path) -> None:
        """
        Test pushing only a specific file using -f flag.

        Directory structure:
            tmp_path/
            ├── project/
            │   ├── .tsync.yaml
            │   ├── sync_me.txt       <- only this synced
            │   └── ignore_me.txt     <- not synced
            └── backup/

        After push -f sync_me.txt:
            tmp_path/
            └── backup/
                └── sync_me.txt       <- only this file
        """
        project = tmp_path / "project"
        project.mkdir()
        (project / "sync_me.txt").write_text("sync this")
        (project / "ignore_me.txt").write_text("ignore this")

        backup = tmp_path / "backup"
        backup.mkdir()

        config = project / ".tsync.yaml"
        config.write_text(f"remotes:\n  backup: {backup}\n")

        result = subprocess.run(
            [sys.executable, "-m", "tsync.cli", "push", "backup", "-y",
             "-f", "sync_me.txt"],
            cwd=project,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Push failed: {result.stderr}"
        assert (backup / "sync_me.txt").exists()
        assert (backup / "sync_me.txt").read_text() == "sync this"
        assert not (backup / "ignore_me.txt").exists()

    def test_push_specific_folder_with_f_flag(self, tmp_path: Path) -> None:
        """
        Test pushing only a specific folder using -f flag.

        Directory structure:
            tmp_path/
            ├── project/
            │   ├── .tsync.yaml
            │   ├── root.txt              <- not synced
            │   └── subdir/
            │       ├── a.txt             <- synced
            │       └── b.txt             <- synced
            └── backup/

        After push -f subdir:
            tmp_path/
            └── backup/
                └── subdir/
                    ├── a.txt
                    └── b.txt
        """
        project = tmp_path / "project"
        project.mkdir()
        (project / "root.txt").write_text("root file")
        subdir = project / "subdir"
        subdir.mkdir()
        (subdir / "a.txt").write_text("file a")
        (subdir / "b.txt").write_text("file b")

        backup = tmp_path / "backup"
        backup.mkdir()

        config = project / ".tsync.yaml"
        config.write_text(f"remotes:\n  backup: {backup}\n")

        result = subprocess.run(
            [sys.executable, "-m", "tsync.cli", "push", "backup", "-y",
             "-f", "subdir"],
            cwd=project,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Push failed: {result.stderr}"
        assert not (backup / "root.txt").exists()
        assert (backup / "subdir" / "a.txt").exists()
        assert (backup / "subdir" / "b.txt").exists()

    def test_pull_specific_file_with_f_flag(self, tmp_path: Path) -> None:
        """
        Test pulling only a specific file using -f flag.

        Directory structure before:
            tmp_path/
            ├── project/
            │   └── .tsync.yaml
            └── backup/
                ├── wanted.txt        <- pull only this
                └── unwanted.txt      <- don't pull

        Directory structure after pull -f wanted.txt:
            tmp_path/
            ├── project/
            │   ├── .tsync.yaml
            │   └── wanted.txt        <- pulled
            └── backup/
                └── ...
        """
        project = tmp_path / "project"
        project.mkdir()

        backup = tmp_path / "backup"
        backup.mkdir()
        (backup / "wanted.txt").write_text("wanted content")
        (backup / "unwanted.txt").write_text("unwanted content")

        config = project / ".tsync.yaml"
        config.write_text(f"remotes:\n  backup: {backup}\n")

        result = subprocess.run(
            [sys.executable, "-m", "tsync.cli", "pull", "backup", "-y",
             "-f", "wanted.txt"],
            cwd=project,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Pull failed: {result.stderr}"
        assert (project / "wanted.txt").exists()
        assert (project / "wanted.txt").read_text() == "wanted content"
        assert not (project / "unwanted.txt").exists()
