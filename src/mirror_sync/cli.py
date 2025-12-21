"""
mirror-sync: rsync wrapper to mirror files between local machine and remotes.

The closest parent directory containing .mirrors.yaml is considered the root
directory. Remotes are defined in .mirrors.yaml:

```yaml
remotes:
    server1: user@host:/path/to/root
    server2: hostname:/path/to/root  # if host is in ssh config
    local_backup: /path/to/local/root
excludes:
    - __pycache__
    - .git
    - "*.pyc"
includes:
    - important.pyc  # override excludes for specific patterns
```

Usage:
    mirror-sync push [OPTIONS] REMOTES...
    mirror-sync pull [OPTIONS] REMOTE
    mirror-sync diff REMOTE [--copy]
    mirror-sync cmd --target REMOTES... COMMAND...
    mirror-sync edit
"""

import argparse
import difflib
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from hashlib import md5
from pathlib import Path

import magic
from deepdiff import DeepDiff
from rich import print
from rich.pretty import pprint
from ruamel.yaml import YAML

yaml = YAML(typ='safe')

SYNC_COMMAND = 'rsync'
SYNC_ARGS_BASE = ('-avzhPr',)


@dataclass
class Config:
    remotes: dict[str, str] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    includes: list[str] = field(default_factory=list)
    mode: str = field(default_factory=str)
    dry_run: bool = False
    mkdir: bool = False
    delete: bool = False
    delete_excluded: bool = False

    def update(self, in_dict):
        for key in self.__dataclass_fields__:
            if not in_dict.get(key):
                continue
            self.__dict__[key] = in_dict.get(key)


def findup(name: str, path: Path) -> Path | None:
    """Find the nearest parent directory containing a file with the given name."""
    while str(path) != '/':
        files = [p.name for p in path.glob('*') if p.is_file()]
        if name in files:
            return path
        path = path.parent
    return None


def find_and_parse(filename: str = ".mirrors.yaml", parser=yaml.load):
    """Find and parse the nearest .mirrors.yaml config file."""
    cwd = Path().resolve()
    root = findup(filename, cwd)
    if root is not None:
        print("found root: ", root)
        return root, parser(root / filename)
    return None, {}


def find_files(target_dir: str = '.', ignore_files: list = None, ignore_dirs: list = None) -> list[Path]:
    """Recursively find all files in target_dir, excluding specified files and directories."""
    if ignore_files is None:
        ignore_files = []
    if ignore_dirs is None:
        ignore_dirs = ['.git']

    files = [p for p in Path(target_dir).rglob('*') if p.is_file()]

    # Filter out files in ignored directories (check by directory name)
    for ignore_dir in ignore_dirs:
        files = [f for f in files if ignore_dir not in [p.name for p in f.parents]]

    # Filter out ignored files (check by filename)
    for ignore_file in ignore_files:
        files = [f for f in files if f.name != ignore_file]

    return files


def resolve_remote(remotestr: str) -> str:
    """
    Resolve a remote string. Currently a passthrough that validates format.

    Accepts:
        - SSH remotes: user@host:/path or host:/path (if host is in ssh config)
        - Local paths: /absolute/path
    """
    return remotestr


def get_file_hashes(directory: str) -> dict[str, str]:
    """Compute MD5 hashes for all files in directory."""
    files = find_files(directory)
    hashes = {}
    for ifile in files:
        with open(ifile, 'rb') as fp:
            file_hash = md5(fp.read()).hexdigest()
            relpath = Path(ifile).relative_to(directory)
            hashes[relpath.as_posix()] = file_hash
    return hashes


def show_diff(state1: dict, state2: dict, path1: Path = None, path2: Path = None):
    """Display differences between two file hash states with detailed diffs for text files."""
    diff = DeepDiff(state1, state2)
    if not diff:
        print("Clean!")
        return

    if 'dictionary_item_added' in diff:
        added = [x[6:-2] for x in diff['dictionary_item_added']]
        print("[green]ADDED[/green]:", *added)

    if 'dictionary_item_removed' in diff:
        removed = [x[6:-2] for x in diff['dictionary_item_removed']]
        print("[red]REMOVED[/red]:", *removed)

    if 'values_changed' in diff:
        changed = {x[6:-2]: diff['values_changed'][x] for x in diff['values_changed']}
        print("[yellow]CHANGED[/yellow]:", *changed)

        # Handle gzip archives
        if path1 and path1.is_file() and path1.suffix == '.gz':
            with tarfile.open(path1, 'r:gz') as tar:
                tar.extractall(Path('/tmp') / "path1", members=[x for x in tar.getmembers() if x.name in changed])
            path1 = Path("/tmp/path1")

        if path2 and path2.is_file() and path2.suffix == '.gz':
            with tarfile.open(path2, 'r:gz') as tar:
                tar.extractall(Path('/tmp') / "path2", members=[x for x in tar.getmembers() if x.name in changed])
            path2 = Path("/tmp/path2")

        # Show detailed diff for text files
        if path1 and path2:
            mime = magic.Magic(mime=True)
            allowed_types = ['text/plain', 'text/csv', 'application/json']

            for changed_file in changed:
                mimetype = mime.from_file(str(path1 / changed_file))
                if mimetype in allowed_types:
                    with open(path1 / changed_file, 'r') as fp:
                        oldfile = fp.read().split('\n')
                    with open(path2 / changed_file, 'r') as fp:
                        newfile = fp.read().split('\n')
                    out = list(difflib.unified_diff(
                        oldfile, newfile,
                        fromfile=str(path1 / changed_file),
                        tofile=str(path2 / changed_file),
                        n=0
                    ))
                    for line in out:
                        if line.startswith('-'):
                            print('[red]' + line)
                        elif line.startswith('+'):
                            print('[green]' + line)

    sys.exit(1)


def push(files: list, targets: list, remotes: dict, root: Path, sync_args: list, mkdir: bool = False):
    """Push files to one or more remote targets."""
    sync_args = list(sync_args) + ['--relative']
    if targets == ['all']:
        targets = list(remotes.keys())

    for target in targets:
        if target not in remotes:
            raise RuntimeError(f"Remote {target} not found. Please check {root}/.mirrors.yaml")

        sync_args_local = sync_args[:]
        if files:
            sync_args_local.extend(files)
        else:
            sync_args_local.append('.')

        remote_path = str(Path(remotes[target]) / Path('.').resolve().relative_to(str(root))) + os.sep
        sync_args_local.append(remote_path)

        if mkdir:
            mkdir_path = str(Path(remotes[target])).split(":")[-1]
            mkdir_path = str(Path(mkdir_path) / Path(".").resolve().relative_to(str(root))) + os.sep
            mkdir_cmd = f'mkdir -p {mkdir_path}'
            if ':' in remotes[target]:
                mkdir_cmd = f'ssh {target} {mkdir_cmd}'
            run_command(mkdir_cmd.split())

        run_command([SYNC_COMMAND] + sync_args_local)


def pull(files: list, source: str, remotes: dict, root: Path, sync_args: list):
    """Pull files from a remote source."""
    if source not in remotes:
        return

    sync_args_local = list(sync_args)
    if files:
        sync_args_local.append('--relative')
        for file in files:
            # The additional dot ensures directory structure relative to current directory
            sync_args_local.append(
                str(Path(remotes[source]).as_posix() / Path('.').resolve().relative_to(str(root))) + '/./' + file
            )
    else:
        sync_args_local.append(str(Path(remotes[source]) / Path('.').resolve().relative_to(str(root))) + os.sep)

    sync_args_local.append('.')
    run_command([SYNC_COMMAND] + sync_args_local)


def run_command(cmdlist: list[str], capture_output: bool = False) -> subprocess.CompletedProcess:
    """Execute a command and return the result."""
    print(cmdlist)
    return subprocess.run(cmdlist, capture_output=capture_output, text=True)


def run_ssh_command(remote: str, command: str, directory: str) -> subprocess.CompletedProcess:
    """Execute a command on a remote host via SSH."""
    cmd = ['ssh', remote, f"cd {Path(directory).as_posix()} && {command}"]
    return run_command(cmd)


def parse_args():
    """Parse command line arguments."""
    ap = argparse.ArgumentParser(
        prog='mirror-sync',
        description='rsync wrapper for mirroring files with push/pull workflow'
    )

    subparsers = ap.add_subparsers(dest='mode')

    # Push subcommand
    sub_push = subparsers.add_parser('push', help='Push to one or more remotes')
    sub_push.add_argument('target', nargs='*', help="Target remote(s)")
    sub_push.add_argument('-f', '--files', nargs='*', help='Files or directories to sync')
    sub_push.add_argument('-e', '--excludes', nargs='*', action='extend', default=[], help='Exclude PATTERN')
    sub_push.add_argument('-i', '--includes', nargs='*', action='extend', default=[],
                          help='Include PATTERN (overrides excludes)')

    # Pull subcommand
    sub_pull = subparsers.add_parser('pull', help='Pull from a remote')
    sub_pull.add_argument('source', nargs='?', help="Source remote")
    sub_pull.add_argument('-f', '--files', nargs='*', help='Files or directories to sync')
    sub_pull.add_argument('-e', '--excludes', nargs='*', action='extend', default=[], help='Exclude PATTERN')
    sub_pull.add_argument('-i', '--includes', nargs='*', action='extend', default=[],
                          help='Include PATTERN (overrides excludes)')

    # Cmd subcommand
    sub_cmd = subparsers.add_parser('cmd', help='Run command on remote in corresponding directory')
    sub_cmd.add_argument('command', nargs='*', help='Command to execute')
    sub_cmd.add_argument('--target', nargs='*', help='Target remote(s)')

    # Diff subcommand
    sub_diff = subparsers.add_parser('diff', help='Show differences with remote')
    sub_diff.add_argument('remote', help="Remote to compare with")
    sub_diff.add_argument('--copy', action='store_true',
                          help="Copy remote files to temp directory and show detailed diff")

    # Edit subcommand
    subparsers.add_parser('edit', help='Edit nearest .mirrors.yaml config')

    # Global options
    ap.add_argument('-ne', '--no-excludes', action='store_true', default=False, help='Ignore excludes')
    ap.add_argument("-y", "--no-confirm", action='store_true', help="Don't prompt before syncing")
    ap.add_argument("-d", "--dry-run", action='store_true', help="Show commands without executing")
    ap.add_argument("-m", "--mkdir", action='store_true', help="Create directories before sync")
    ap.add_argument("--delete", action='store_true',
                    help="Delete extraneous files on receiver (directories only)")
    ap.add_argument("--delete-excluded", action='store_true', help="Also delete excluded files")

    # Multi-pass parsing for flexible argument ordering
    args, extra_args_list = ap.parse_known_args()
    args, extra_args_list = ap.parse_known_args(extra_args_list, args)
    return args, extra_args_list


def main():
    """Main entry point."""
    args, extra_args_list = parse_args()
    config = Config()

    root = None
    root_yaml, config_yaml = find_and_parse('.mirrors.yaml', yaml.load)
    if config_yaml:
        config.update(config_yaml)
        root = root_yaml

    config.update(vars(args))

    if config.remotes:
        for tag, remote_path in config.remotes.items():
            if ((config.mode == 'push' and tag in (args.target or [])) or
                    (config.mode == 'pull' and tag == args.source) or
                    (config.mode == 'diff' and tag == args.remote)):
                config.remotes[tag] = resolve_remote(remote_path)

    if root:
        pprint(config, expand_all=True)
    else:
        return

    if not config.mode:
        return

    sync_args = list(SYNC_ARGS_BASE)

    if config.delete:
        sync_args.append('--delete')

    if config.delete_excluded:
        sync_args.append('--delete-excluded')

    if config.dry_run:
        sync_args.append('--dry-run')

    # Include must come before exclude for rsync
    if not args.no_excludes:
        for item in config.includes:
            sync_args.append(f"--include={item}")
        for item in config.excludes:
            sync_args.append(f"--exclude={item}")

    print(f"Unprocessed args directly passed to {SYNC_COMMAND}: {extra_args_list}")
    sync_args.extend(extra_args_list)

    if config.mode == 'push' and args.target:
        assert all(t in config.remotes.keys() for t in args.target)
        warn = 'y' if args.no_confirm else input(f"PUSH to {args.target}? (y/Y/ENTER to continue)")
        if warn.lower() == 'y' or warn == '':
            push(config.files, args.target, config.remotes, root, sync_args, mkdir=config.mkdir)

    elif config.mode == 'pull' and args.source:
        assert args.source in config.remotes.keys()
        warn = 'y' if args.no_confirm else input(f"PULL from {args.source}? (y/Y/ENTER to continue)")
        if warn.lower() == 'y' or warn == '':
            pull(config.files, args.source, config.remotes, root, sync_args)

    elif config.mode == 'diff':
        remote_path = str(Path(config.remotes[args.remote]) / Path('.').resolve().relative_to(str(root))) + os.sep
        if args.copy:
            sync_args_pass1 = ['-ar', '--dry-run', '--checksum', '--out-format="%f"', '.', remote_path]
            with tempfile.TemporaryDirectory() as tempdir:
                out = run_command([SYNC_COMMAND] + sync_args_pass1, capture_output=True)
                changed_files = filter(None, out.stdout.replace('"', '').split('\n'))
                changed_files = list(filter(lambda x: x != '.', changed_files))
                changed_files = [remote_path + f for f in changed_files]
                print(changed_files)
                if changed_files:
                    run_command([SYNC_COMMAND, '-avzhPrc'] + changed_files + [tempdir])
                    remote_hashes = get_file_hashes(tempdir)
                    local_hashes = get_file_hashes('.')
                    print(remote_hashes)
                    print(local_hashes)
                    show_diff(local_hashes, remote_hashes, path1=Path('.'), path2=Path(tempdir))
        else:
            run_command([SYNC_COMMAND, '-arnci', '.', remote_path])
            run_command([SYNC_COMMAND, '-arnci', remote_path, '.'])

    elif config.mode == 'edit':
        editor = os.environ.get('EDITOR', 'vi')
        config_path = (root / '.mirrors.yaml').as_posix()
        print(f"Opening config file: {config_path}")
        time.sleep(1)
        run_command([editor, config_path])

    elif config.mode == 'cmd':
        assert all(t in config.remotes.keys() for t in args.target)
        for target in args.target:
            remotedir = str(Path(config.remotes[target]) / Path('.').resolve().relative_to(str(root))) + os.sep
            if ':' in remotedir:
                remote, directory = remotedir.split(':')
                res = run_ssh_command(remote, ' '.join(args.command), directory)
                sys.exit(res.returncode)
            else:
                res = run_command(args.command)
                sys.exit(res.returncode)


if __name__ == "__main__":
    main()
