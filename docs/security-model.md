# Security Model

## Design Principle: Whitelist, Don't Blacklist

Instead of trying to filter dangerous commands (impossible to do perfectly), we only expose **specific, safe diagnostic operations**.

## What You CANNOT Do

- ❌ Execute arbitrary shell commands
- ❌ Read sensitive system files (`/etc/shadow`, `.env`, etc.)
- ❌ Modify disk contents (`dd`, `mkfs`, `fdisk`, etc.)
- ❌ Mount or unmount filesystems
- ❌ Change file permissions or ownership
- ❌ Run shells (`bash`, `python`, `perl`, etc.)
- ❌ Download or execute remote code
- ❌ Escalate privileges (`sudo`, `su`)

## What You CAN Do

- ✅ List storage devices (`lsblk -d`)
- ✅ Read SMART data (`smartctl -j -a`)
- ✅ Read NVMe health (`nvme smart-log`)
- ✅ Run SMART self-tests (`smartctl -t short/long/conveyance`)
- ✅ Check ZFS status (`zpool status`, `zfs list`)
- ✅ Check RAID status (`mdadm --detail`, `cat /proc/mdstat`)
- ✅ Check I/O stats (`iostat -x`)
- ✅ Query Prometheus (read-only API)
- ✅ Query InfluxDB (read-only SELECT queries)

## Security Controls

### 1. No Generic Command Execution

There is no `exec_command()` tool. Only specific, pre-defined diagnostic operations are exposed as MCP tools.

### 2. Device Name Validation

Device names must match a strict pattern:

```python
DEVICE_NAME_PATTERN = r"^(sd[a-z]|nvme\d+n\d+|vd[a-z]|hd[a-z]|mmcblk\d+(p\d+)?|dm-\d+)$"
```

This blocks:
- Path traversal (`../../etc`)
- Command injection (`sda;rm -rf /`)
- Absolute paths (`/dev/sda`)
- Partition paths (`sda1` — only whole disks)

### 3. Command Whitelist

Commands must start with an approved prefix:

```python
SAFE_COMMAND_PREFIXES = [
    "smartctl -",
    "nvme smart-log",
    "lsblk -d",
    "zpool status",
    "cat /proc/mdstat",
    "mdadm --detail",
    "iostat -x",
    # ... etc
]
```

### 4. Dangerous Command Blocklist

Even if a command matches a safe prefix, it's checked against a blocklist:

```python
DANGEROUS_COMMAND_PATTERNS = [
    "sudo", "su ", "chmod", "chown", "mount ", "mkfs",
    "fdisk", "dd ", "wget ", "curl ", "bash ", "python ",
    # ... etc
]
```

### 5. SMART Tests Are Safe

SMART self-tests (`smartctl -t short/long/conveyance`) are **read-only diagnostic operations**. They scan the media for errors but do not modify data on the drive.

### 6. Input Sanitization

All inputs are sanitized against:
- Command separators (`;`, `|`, `&`, `&&`, `||`)
- Command substitution (backticks, `$()`)
- Variable expansion (`$VAR`)
- Shell redirection (`>`, `<`)
- Quotes (`'`, `"`)
- Newlines and special characters

### 7. Data Source Security

- **Prometheus**: Read-only queries only. Shell injection characters blocked.
- **InfluxDB**: SELECT-only enforcement. SQL injection characters blocked.
- **Host (SSH)**: Command whitelist enforced. No generic execution.

## Why No `exec_command()`

Five reasons:

1. **Impossible to filter perfectly** — Shell has dozens of injection vectors
2. **Privilege escalation** — `sudo smartctl` vs `sudo cat /etc/shadow`
3. **Data destruction** — `dd`, `mkfs` can erase drives instantly
4. **Lateral movement** — SSH pivoting from compromised host
5. **Audit trail** — Specific tools have clear purpose and logging

## Adding Safe Tools

When adding a new diagnostic tool:

1. Add the command prefix to `SAFE_COMMAND_PREFIXES` in `security.py`
2. Validate all inputs (device names, test types, etc.)
3. Write security tests first (TDD)
4. Document the tool in `docs/tools.md`
