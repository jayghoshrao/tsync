"""Tests for mirror-sync CLI."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from mirror_sync.cli import (
    Config,
    findup,
    find_files,
    get_file_hashes,
    resolve_remote,
    parse_args,
)


class TestFindup:
    """Tests for findup function."""

    def test_finds_file_in_current_dir(self, tmp_path):
        (tmp_path / "target.yaml").touch()
        result = findup("target.yaml", tmp_path)
        assert result == tmp_path

    def test_finds_file_in_parent_dir(self, tmp_path):
        (tmp_path / "target.yaml").touch()
        subdir = tmp_path / "sub" / "deep"
        subdir.mkdir(parents=True)
        result = findup("target.yaml", subdir)
        assert result == tmp_path

    def test_returns_none_when_not_found(self, tmp_path):
        result = findup("nonexistent.yaml", tmp_path)
        assert result is None

    def test_stops_at_root(self, tmp_path):
        # Create a deep path without the target file
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        result = findup("nonexistent.yaml", deep)
        assert result is None


class TestFindFiles:
    """Tests for find_files function."""

    def test_finds_all_files(self, tmp_path):
        (tmp_path / "a.txt").touch()
        (tmp_path / "b.txt").touch()
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "c.txt").touch()

        files = find_files(str(tmp_path))
        assert len(files) == 3

    def test_ignores_git_by_default(self, tmp_path):
        (tmp_path / "a.txt").touch()
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").touch()

        files = find_files(str(tmp_path))
        filenames = [f.name for f in files]
        assert "a.txt" in filenames
        assert "config" not in filenames

    def test_ignores_specified_dirs(self, tmp_path):
        (tmp_path / "a.txt").touch()
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "module.pyc").touch()

        files = find_files(str(tmp_path), ignore_dirs=['.git', '__pycache__'])
        filenames = [f.name for f in files]
        assert "a.txt" in filenames
        assert "module.pyc" not in filenames

    def test_ignores_specified_files(self, tmp_path):
        (tmp_path / "keep.txt").touch()
        (tmp_path / "ignore.txt").touch()

        files = find_files(str(tmp_path), ignore_files=['ignore.txt'], ignore_dirs=[])
        filenames = [f.name for f in files]
        assert "keep.txt" in filenames
        assert "ignore.txt" not in filenames


class TestGetFileHashes:
    """Tests for get_file_hashes function."""

    def test_computes_hashes(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "b.txt").write_text("world")

        hashes = get_file_hashes(str(tmp_path))
        assert len(hashes) == 2
        assert "a.txt" in hashes
        assert "b.txt" in hashes
        # MD5 of "hello"
        assert hashes["a.txt"] == "5d41402abc4b2a76b9719d911017c592"

    def test_relative_paths(self, tmp_path):
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "file.txt").write_text("test")

        hashes = get_file_hashes(str(tmp_path))
        assert "sub/file.txt" in hashes


class TestResolveRemote:
    """Tests for resolve_remote function."""

    def test_passthrough_ssh_remote(self):
        assert resolve_remote("user@host:/path") == "user@host:/path"

    def test_passthrough_local_path(self):
        assert resolve_remote("/local/path") == "/local/path"

    def test_passthrough_ssh_config_host(self):
        assert resolve_remote("hostname:/path") == "hostname:/path"


class TestConfig:
    """Tests for Config dataclass."""

    def test_default_values(self):
        config = Config()
        assert config.remotes == {}
        assert config.files == []
        assert config.excludes == []
        assert config.includes == []
        assert config.dry_run is False

    def test_update_from_dict(self):
        config = Config()
        config.update({
            'remotes': {'server': 'host:/path'},
            'excludes': ['*.pyc'],
            'dry_run': True,
        })
        assert config.remotes == {'server': 'host:/path'}
        assert config.excludes == ['*.pyc']
        assert config.dry_run is True

    def test_update_ignores_missing_keys(self):
        config = Config()
        config.update({'nonexistent': 'value'})
        # Should not raise, just ignore


class TestParseArgs:
    """Tests for argument parsing."""

    def test_push_with_targets(self):
        with patch('sys.argv', ['mirror-sync', 'push', 'server1', 'server2']):
            args, _ = parse_args()
            assert args.mode == 'push'
            assert args.target == ['server1', 'server2']

    def test_pull_with_source(self):
        with patch('sys.argv', ['mirror-sync', 'pull', 'server']):
            args, _ = parse_args()
            assert args.mode == 'pull'
            assert args.source == 'server'

    def test_diff_mode(self):
        with patch('sys.argv', ['mirror-sync', 'diff', 'server']):
            args, _ = parse_args()
            assert args.mode == 'diff'
            assert args.remote == 'server'

    def test_global_options(self):
        with patch('sys.argv', ['mirror-sync', '-y', '-d', 'push', 'server']):
            args, _ = parse_args()
            assert args.no_confirm is True
            assert args.dry_run is True

    def test_extra_args_passthrough(self):
        # Extra args that aren't recognized get passed through
        with patch('sys.argv', ['mirror-sync', 'push', 'server', '--bwlimit=1000']):
            args, extra = parse_args()
            assert '--bwlimit=1000' in extra


# Integration tests require rsync and actual filesystem operations
# These can be run with: pytest tests/ -m integration

@pytest.mark.integration
class TestIntegration:
    """Integration tests using local paths only."""

    def test_push_to_local_path(self, tmp_path):
        """Test pushing to a local directory."""
        # Setup source and dest directories
        src = tmp_path / "source"
        src.mkdir()
        (src / "file.txt").write_text("content")

        dest = tmp_path / "dest"
        dest.mkdir()

        # Create .mirrors.yaml
        config = src / ".mirrors.yaml"
        config.write_text(f"remotes:\n  backup: {dest}\n")

        # This would require running the actual CLI
        # For now, we just verify the setup is correct
        assert config.exists()
        assert (src / "file.txt").exists()
