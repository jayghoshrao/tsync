# tsync

An rsync wrapper for syncing entire or partial directory trees between machines
with a simple push/pull workflow.

> [!TIP]
> `tsync` can be invoked from any nested subdirectory to synchronize the entire tree.
> Just define your remotes in `.tsync.yaml` at the root, then `tsync push server` from anywhere in the tree.

## Use cases
- Sync source code when developing and testing distributed applications.
    - git commit/push/pull loop works, but is not a good fit when developing.
- Sync code to servers that have restrictions on storing private keys (can't pull).
- Push data+configs, process on another machine, pull only relevant results.
- Data clone, or a crude backup.

## Features

- **Push/Pull workflow**: Sync files to multiple remotes or pull from a single source
- **Config-based remotes**: Define remotes once in `.tsync.yaml`, use by name
- **Directory structure preservation**: Maintains relative paths from the config root
- **Flexible excludes/includes**: Configure patterns in YAML or override via CLI
- **Diff with remotes**: Compare local and remote states before syncing
- **Remote command execution**: Run commands on remotes in the corresponding directory

## Installation

```bash
pip install tsync
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install tsync    # install globally as a CLI tool
uvx tsync                 # run without installing
```

Or install from source:

```bash
git clone https://github.com/jayghoshter/tsync.git
cd tsync
pip install -e .
```

## Quick Start

1. Create a `.tsync.yaml` in your project root:

```yaml
remotes:
    server: user@hostname:/path/to/backup
    nas: nas:/volume1/projects  # if 'nas' is in ssh config
    local_backup: /mnt/backup/projects

excludes:
    - __pycache__
    - .git
    - "*.pyc"
    - node_modules

includes:
    - important.pyc  # override excludes for specific files
```

2. Push to a remote:

```bash
cd /path/to/project/subdir
tsync push server
```

This syncs the current directory to `user@hostname:/path/to/backup/subdir/`.

3. Pull from a remote:

```bash
tsync pull server
```

## Usage

### Push

Push current directory to one or more remotes:

```bash
tsync push server              # push to 'server'
tsync push server nas          # push to multiple remotes
tsync push all                 # push to all configured remotes
tsync push server -f file.txt  # push specific files only
```

### Pull

Pull from a remote to current directory:

```bash
tsync pull server
tsync pull server -f file.txt  # pull specific files only
```

### Diff

Compare local and remote states:

```bash
tsync diff server           # quick rsync-based diff
tsync diff server --copy    # detailed diff with file contents
```

### Remote Commands

Run commands on remotes in the corresponding directory:

```bash
tsync cmd --target server -- ls -la
tsync cmd --target server -- git status
```

### Edit Config

Open the nearest `.tsync.yaml` in your editor:

```bash
tsync edit
```

## Options

| Option | Description |
|--------|-------------|
| `-y, --no-confirm` | Don't prompt before syncing |
| `-d, --dry-run` | Show what would be synced without doing it |
| `-m, --mkdir` | Create remote directories before syncing |
| `--delete` | Delete extraneous files on receiver |
| `--delete-excluded` | Also delete excluded files on receiver |
| `-ne, --no-excludes` | Ignore all exclude patterns |
| `-e, --excludes` | Additional exclude patterns |
| `-i, --includes` | Additional include patterns |
| `-f, --files` | Specific files/directories to sync |

Additional rsync options can be passed directly:

```bash
tsync push server -- --compress-level=9
```

## Configuration

The `.tsync.yaml` file is searched upward from the current directory. The directory containing the config file is considered the "root" - all relative paths are computed from there.

```yaml
remotes:
    # SSH remotes (rsync over SSH)
    server: user@host:/path/to/root
    shortname: hostname:/path/to/root  # uses ssh config

    # Local paths
    backup: /mnt/external/backup

excludes:
    - __pycache__
    - "*.pyc"
    - .git
    - .env
    - node_modules

includes:
    - .env.example  # include despite .env exclude pattern
```

## How It Works

1. Searches upward for `.tsync.yaml` to find the project root
2. Computes the relative path from root to current directory
3. Constructs the remote path: `<remote_root>/<relative_path>/`
4. Runs rsync with the configured options

This means if you're in `/home/user/projects/myapp/src/` and the `.tsync.yaml` is in `/home/user/projects/myapp/`, syncing to `server: host:/backup` will target `host:/backup/src/`.

## Requirements

- Python 3.10+
- rsync (installed on both local and remote machines)
- SSH access to remote machines (for SSH remotes)

## License

MIT
