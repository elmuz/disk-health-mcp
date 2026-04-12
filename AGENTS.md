# Disk Health MCP - Agent Guide

## Project Overview

**disk-health-mcp** is a disk health diagnostics MCP server. It provides AI assistants with controlled access to SMART, NVMe, ZFS, RAID, and I/O diagnostics with manufacturer-aware normalization.

This is the **main project** in this directory, not a subagent. It's a complete MCP server that can be used with Qwen Code, Claude Desktop, or any MCP client.

## Dependencies

| Dependency | Purpose |
|------------|---------|
| `server-management-lib` | SSH management, security validation, InfluxDB/Prometheus clients |
| `mcp` | MCP server framework |

`server-management-lib` is maintained separately:
<https://github.com/elmuz/server-management-lib>

## Development Workflow

### Quick Start

```bash
# Install with dev dependencies
uv pip install -e ".[dev]" --python .venv

# Install pre-commit hooks
pre-commit install
```

### Development Commands

```bash
# Run tests
uv run pytest tests/ -v

# Run linting
uv run ruff check .

# Fix linting issues
uv run ruff check --fix .

# Format code
uv run ruff format .

# Type checking
uv run ty check

# Run all checks (what pre-commit does)
uv run ruff check . && uv run ruff format . && uv run ty check && uv run pytest tests/ -v
```

### Pre-commit Hooks

Pre-commit hooks run automatically on `git commit`:

- **ruff**: Linting and formatting
- **ty**: Type checking
- **pytest**: Run all tests

To run manually:

```bash
pre-commit run --all-files
```

## Security Philosophy

**Zero-trust design — delegated to server-management-lib:**
- ❌ NO generic command execution
- ✅ ONLY specific, whitelisted diagnostic commands
- 🔒 All device names validated against safe patterns
- 🛡️ Command whitelist enforced before execution
- 🛡️ InfluxDB/Prometheus queries are read-only with injection prevention

See `README.md` for full security documentation.

## Quick Start (Server)

### 1. Install

```bash
uv pip install -e . --python .venv
```

### 2. Configure

```bash
cp config.example.yaml config.yaml
nano config.yaml  # Add your SSH details
```

### 3. Test

```bash
uv run pytest tests/ -v
uv run python test_tools.py
```

### 4. Use with Qwen Code

Already configured in `.qwen/settings.json`. Just restart Qwen Code!

## Available Tools

| Tool | Purpose |
|------|---------|
| `list_disks` | List all storage devices |
| `get_disk_health` | Full health analysis with severity |
| `get_smart_attributes` | Raw SMART attributes table |
| `get_nvme_health` | NVMe SMART health log |
| `run_smart_test` | Trigger SMART self-test |
| `get_zfs_status` | ZFS pool health |
| `get_raid_status` | mdadm RAID status |
| `get_io_stats` | I/O utilization and latency |
| `query_prometheus_disk` | Prometheus disk metrics |
| `query_influxdb_disk` | InfluxDB historical trends |
| `get_full_disk_report` | Comprehensive report |

## Architecture

```text
src/disk_health_mcp/
├── server.py              # MCP server (11 tools)
├── smart_parser.py        # SMART parser + normalizer
└── __init__.py            # Package entry point

tests/
├── test_security.py       # Security validation tests
└── test_smart_parser.py   # SMART parser tests
```

Shared infrastructure (server-management-lib):
- `SecurityValidator` — Command safety, device names, query validation
- `SSHManager` — Secure SSH connections with whitelisted commands
- `InfluxDBClient` / `PrometheusClient` — HTTP clients for time-series databases

## Security Controls

1. **No exec_command()** — Removed entirely
2. **Device name validation** — Regex pattern matching only
3. **Command whitelist** — Only specific diagnostic commands
4. **Command injection prevention** — All inputs sanitized
5. **SMART tests are safe** — Self-tests don't modify data
6. **Read-only queries** — InfluxDB/Prometheus block write operations

## Testing

```bash
# All tests
uv run pytest tests/ -v

# Coverage report
uv run pytest tests/ --cov=disk_health_mcp
```

## What is MCP?

**MCP (Model Context Protocol)** is an open standard for connecting AI assistants to external tools. Works with:
- Qwen Code ✅
- Claude Desktop ✅
- Any MCP client ✅

Learn more: [modelcontextprotocol.io](https://modelcontextprotocol.io)

## Development

### Adding New Tools

Add a decorated function in `server.py`:

```python
@mcp.tool()
async def my_new_tool(device: str) -> str:
    """Description of the tool."""
    if not security.validate_device_name(device):
        return "❌ Invalid device name"

    cmd = f"smartctl -j -a /dev/{device} 2>&1"
    if not security.is_command_safe(cmd):
        return "❌ Command not in whitelist"
    try:
        await ssh_manager.connect()
        return await ssh_manager.execute_safe_command(cmd)
    finally:
        await ssh_manager.disconnect()
```

### Security Requirements

All tools MUST:
1. Validate device names with `security.validate_device_name()`
2. Use whitelisted commands via `security.is_command_safe()`
3. Use `ssh_manager` for SSH execution (never raw command execution)
4. NOT expose sensitive data
5. NOT allow command injection

### Pre-commit Verification

**Every time you finalize a feature or bugfix, run the full check suite:**

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pymarkdown -c .pymarkdown scan . && uv run python scripts/check_md_links.py && uv run pytest tests/ -v --tb=short
```

This runs all pre-commit hooks in order:

| Check | Tool | What it catches |
|-------|------|----------------|
| Lint | `ruff check` | Style errors, unused imports, ambiguous characters |
| Format | `ruff format` | Code formatting consistency |
| Types | `ty check` | Type mismatches, invalid assignments |
| Markdown | `pymarkdown scan` | Formatting, heading duplicates, code block languages |
| Links | `check_md_links.py` | Broken relative links and anchors |
| Tests | `pytest` | Regressions, new test coverage |

**All must pass before committing.** If any fail, fix the issues first — never commit on a broken state.

## License

MIT
