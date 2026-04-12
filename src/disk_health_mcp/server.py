"""
MCP Server for Disk Health Diagnostics

Provides AI assistants with controlled access to disk health data:
- SMART data analysis with manufacturer-aware normalization
- NVMe health monitoring
- ZFS and RAID status
- I/O statistics
- Historical metrics from Prometheus/InfluxDB
- SMART self-test management

Security is enforced by server-management-lib (whitelisted commands only,
device name validation, read-only InfluxDB/Prometheus queries).

Design Principle: Whitelist operations, don't blacklist commands.
"""

import asyncio
import json
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from server_management_lib import (
    InfluxDBClient,
    PrometheusClient,
    SecurityValidator,
    SSHManager,
    load_config,
)

from .smart_parser import (
    SmartDevice,
    format_smart_summary,
    parse_smart_json,
    parse_smart_text,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load configuration
config_path = Path(__file__).parent.parent.parent / "config.yaml"
config = load_config(config_path)

# Initialize security validator
security = SecurityValidator(config)

# Initialize SSH manager
ssh_manager = SSHManager(config, security)

# Create MCP server
mcp = FastMCP("disk-health-mcp")


# ============================================================================
# SSH-based tools
# ============================================================================


async def _run_ssh(cmd: str) -> str:
    """Execute a command over SSH, managing connection lifecycle."""
    try:
        await ssh_manager.connect()
        return await ssh_manager.execute_safe_command(cmd)
    except Exception as e:
        return f"❌ SSH error: {e}"
    finally:
        await ssh_manager.disconnect()


@mcp.tool()
async def list_disks() -> str:
    """List all storage devices on the host.

    Returns device names, models, serials, sizes, and types.
    Requires host data source to be enabled in config.yaml.

    Returns:
        List of storage devices in JSON format
    """
    if not config.get("host", {}).get("enabled", False):
        return (
            "❌ Host data source is not enabled.\n"
            "Set 'host.enabled: true' in config.yaml."
        )

    cmd = "lsblk -d -o NAME,MODEL,SERIAL,SIZE,TYPE,TRAN --json 2>&1"
    if not security.is_command_safe(cmd):
        return "❌ Command not in safe whitelist"
    return await _run_ssh(cmd)


@mcp.tool()
async def get_disk_health(device: str) -> str:
    """Get a comprehensive health analysis for a specific disk.

    Parses SMART data with manufacturer-aware normalization:
    - Seagate 48-bit composite value handling
    - Critical attribute detection (reallocated sectors, pending sectors)
    - Health score (0-100)
    - Severity assessment (ok, warning, critical)

    Args:
        device: Block device name (e.g., 'sda', 'nvme0n1')

    Returns:
        Human-readable health assessment with severity flags
    """
    if not config.get("host", {}).get("enabled", False):
        return (
            "❌ Host data source is not enabled.\n"
            "Set 'host.enabled: true' in config.yaml."
        )

    if not security.validate_device_name(device):
        return f"❌ Invalid device name: {device}\nUse format: sda, nvme0n1, vda, etc."

    cmd = f"smartctl -j -a /dev/{device} 2>&1"
    if not security.is_command_safe(cmd):
        return "❌ Command not in safe whitelist"

    raw_output = await _run_ssh(cmd)
    if "❌" in raw_output:
        return raw_output

    try:
        data = json.loads(raw_output)
        smart_device: SmartDevice = parse_smart_json(data)
        return format_smart_summary(smart_device)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning(f"JSON parse failed, falling back to text: {e}")
        text_cmd = f"smartctl -a /dev/{device} 2>&1"
        raw_text = await _run_ssh(text_cmd)
        if "❌" not in raw_text:
            smart_device = parse_smart_text(raw_text)
            return format_smart_summary(smart_device)
        return raw_text


@mcp.tool()
async def get_smart_attributes(device: str) -> str:
    """Get raw SMART attributes for a specific disk.

    Returns all SMART attributes with their values, thresholds,
    and severity assessments.

    Args:
        device: Block device name (e.g., 'sda', 'nvme0n1')

    Returns:
        SMART attribute table with severity annotations
    """
    if not config.get("host", {}).get("enabled", False):
        return (
            "❌ Host data source is not enabled.\n"
            "Set 'host.enabled: true' in config.yaml."
        )

    if not security.validate_device_name(device):
        return f"❌ Invalid device name: {device}"

    cmd = f"smartctl -j -a /dev/{device} 2>&1"
    if not security.is_command_safe(cmd):
        return "❌ Command not in safe whitelist"

    result = await _run_ssh(cmd)
    if "❌" in result:
        return result

    try:
        data = json.loads(result)
        smart_device = parse_smart_json(data)
        lines = [
            f"SMART Attributes for {device} ({smart_device.model})",
            "",
            f"{'ID':<4} {'Name':<28} {'Value':>6} {'Worst':>6} "
            f"{'Thresh':>7} {'Raw':>15} {'Type':<10} {'Severity':<10}",
            "-" * 95,
        ]
        for attr in smart_device.attributes:
            severity_icon = (
                "🔴"
                if attr.severity == "critical"
                else "🟡"
                if attr.severity == "warning"
                else "✅"
            )
            lines.append(
                f"{attr.attr_id:<4} {attr.name:<28} "
                f"{attr.value:>6} {attr.worst:>6} "
                f"{attr.thresh:>7} {attr.raw_value:>15} "
                f"{attr.attr_type:<10} {severity_icon} {attr.severity:<8}"
            )
        return "\n".join(lines)
    except (json.JSONDecodeError, KeyError, TypeError):
        return result


@mcp.tool()
async def get_nvme_health(device: str) -> str:
    """Get NVMe SMART health log for an NVMe device.

    Args:
        device: NVMe device name (e.g., 'nvme0n1')

    Returns:
        NVMe SMART health data
    """
    if not config.get("host", {}).get("enabled", False):
        return (
            "❌ Host data source is not enabled.\n"
            "Set 'host.enabled: true' in config.yaml."
        )

    if not security.validate_device_name(device):
        return f"❌ Invalid device name: {device}"

    cmd = f"nvme smart-log /dev/{device} 2>&1"
    if not security.is_command_safe(cmd):
        return "❌ Command not in safe whitelist"
    return await _run_ssh(cmd)


@mcp.tool()
async def run_smart_test(device: str, test_type: str = "short") -> str:
    """Run a SMART self-test on a disk.

    Self-tests are safe - they don't modify data on the drive.
    Results appear in the SMART self-test log after completion.

    Test types:
    - short: Quick test (~2 minutes). Checks electrical/mechanical + short scan.
    - long: Comprehensive test (hours). Full media scan.
    - conveyance: For drives transported after purchase (~5 minutes).

    Args:
        device: Block device name (e.g., 'sda', 'nvme0n1')
        test_type: Test type - short, long, or conveyance (default: short)

    Returns:
        Test initiation status
    """
    if not config.get("host", {}).get("enabled", False):
        return (
            "❌ Host data source is not enabled.\n"
            "Set 'host.enabled: true' in config.yaml."
        )

    if not security.validate_device_name(device):
        return f"❌ Invalid device name: {device}"

    if not security.validate_smart_test_type(test_type):
        return f"❌ Invalid test type: {test_type}. Use: short, long, conveyance"

    cmd = f"smartctl -t {test_type} /dev/{device} 2>&1"
    if not security.is_command_safe(cmd):
        return "❌ Command not in safe whitelist"
    return await _run_ssh(cmd)


@mcp.tool()
async def get_zfs_status() -> str:
    """Get ZFS pool status, health, and capacity.

    Returns pool names, sizes, allocation, dedup ratio, and health status.
    Also reports scrub status if available.

    Returns:
        ZFS pool status and health information
    """
    if not config.get("host", {}).get("enabled", False):
        return (
            "❌ Host data source is not enabled.\n"
            "Set 'host.enabled: true' in config.yaml."
        )

    cmd = (
        "zpool status -x 2>&1 && echo '---' && "
        "zpool list -o name,size,alloc,free,cap,dedup,health 2>&1"
    )
    if not security.is_command_safe(cmd):
        return "❌ Command not in safe whitelist"
    return await _run_ssh(cmd)


@mcp.tool()
async def get_raid_status() -> str:
    """Get mdadm software RAID status.

    Reports RAID level, array health, device state, and sync progress.
    Also reads /proc/mdstat for active arrays.

    Returns:
        RAID array status and health information
    """
    if not config.get("host", {}).get("enabled", False):
        return (
            "❌ Host data source is not enabled.\n"
            "Set 'host.enabled: true' in config.yaml."
        )

    cmd = "cat /proc/mdstat 2>&1 && echo '---' && mdadm --detail --scan --verbose 2>&1"
    if not security.is_command_safe(cmd):
        return "❌ Command not in safe whitelist"
    return await _run_ssh(cmd)


@mcp.tool()
async def get_io_stats() -> str:
    """Get disk I/O statistics.

    Reports per-device I/O utilization, latency, throughput,
    and queue depth from iostat.

    Returns:
        Extended I/O statistics for all devices
    """
    if not config.get("host", {}).get("enabled", False):
        return (
            "❌ Host data source is not enabled.\n"
            "Set 'host.enabled: true' in config.yaml."
        )

    cmd = "iostat -x 1 1 2>&1"
    if not security.is_command_safe(cmd):
        return "❌ Command not in safe whitelist"
    return await _run_ssh(cmd)


# ============================================================================
# HTTP-based tools (InfluxDB, Prometheus)
# ============================================================================


def _make_influxdb_client() -> InfluxDBClient:
    """Create InfluxDB client from config."""
    cfg = config.get("influxdb", {})
    return InfluxDBClient(
        host=cfg.get("host", "localhost"),
        port=cfg.get("port", 8181),
        use_https=cfg.get("use_https", False),
        database=cfg.get("database"),
        token=cfg.get("token"),
        query_limit=cfg.get("query_limit", 1000),
    )


def _make_prometheus_client() -> PrometheusClient:
    """Create Prometheus client from config."""
    cfg = config.get("prometheus", {})
    return PrometheusClient(
        host=cfg.get("host", "localhost"),
        port=cfg.get("port", 9090),
        use_https=cfg.get("use_https", False),
        token=cfg.get("token"),
    )


@mcp.tool()
async def query_prometheus_disk(query: str) -> str:
    """Query Prometheus for disk-related metrics.

    Useful for checking node_exporter smartmon and disk I/O metrics.

    Common queries:
    - smartmon_health_ok
    - smartmon_temperature_celsius
    - node_disk_io_time_seconds_total

    Args:
        query: PromQL expression for disk metrics

    Returns:
        Query results in JSON format
    """
    prom_config = config.get("prometheus", {})
    if not prom_config.get("enabled", False):
        return (
            "❌ Prometheus is not enabled.\n"
            "Set 'prometheus.enabled: true' in config.yaml."
        )

    validated = security.validate_prometheus_query(query)
    if validated is None:
        return "❌ Invalid PromQL query: contains forbidden characters"

    client = _make_prometheus_client()
    return await client.query(query)


@mcp.tool()
async def query_influxdb_disk(query: str, database: str | None = None) -> str:
    """Query InfluxDB for historical disk metrics.

    Useful for trend analysis: has SMART data been degrading over time?
    Telegraf's smart plugin collects SMART attributes as InfluxDB measurements.

    Args:
        query: SQL query (must start with SELECT)
        database: Database name (overrides config default)

    Returns:
        Query results in JSON format
    """
    influx_config = config.get("influxdb", {})
    if not influx_config.get("enabled", False):
        return (
            "❌ InfluxDB is not enabled.\nSet 'influxdb.enabled: true' in config.yaml."
        )

    validated = security.validate_influxdb_query(query)
    if validated is None:
        return (
            "❌ Invalid query. Queries must:\n"
            "- Start with SELECT (read-only only)\n"
            "- Not contain SQL injection characters (;, --, /*, etc.)\n"
            "- Not contain shell injection characters"
        )

    client = _make_influxdb_client()
    return await client.query(query, database)


# ============================================================================
# Composite tools
# ============================================================================


@mcp.tool()
async def get_full_disk_report(device: str | None = None) -> str:
    """Get a comprehensive disk health report combining multiple data sources.

    If device is specified: detailed analysis of that device.
    If no device: overview of all disks with severity summary.

    This is the recommended tool for overall disk health assessment.

    Args:
        device: Optional specific device name (e.g., 'sda')

    Returns:
        Comprehensive disk health report
    """
    if not config.get("host", {}).get("enabled", False):
        return (
            "❌ Host data source is not enabled.\n"
            "Set 'host.enabled: true' in config.yaml."
        )

    if device:
        if not security.validate_device_name(device):
            return f"❌ Invalid device name: {device}"
        return await get_disk_health(device)

    # Overview of all disks
    lines = ["=== Disk Health Overview ===\n"]

    disk_list = await list_disks()
    if "❌" not in disk_list:
        try:
            data = json.loads(disk_list)
            blockdevs = data.get("blockdevices", [])
            lines.append(f"Found {len(blockdevs)} block devices:\n")
            for dev in blockdevs:
                name = dev.get("name", "")
                model = dev.get("model", "")
                size = dev.get("size", "")
                lines.append(f"  • /dev/{name} - {model} ({size})")
        except (json.JSONDecodeError, KeyError):
            lines.append(f"Disk list: {disk_list[:500]}")

    lines.append("\n=== Individual Health Checks ===\n")

    try:
        data = json.loads(disk_list)
        blockdevs = data.get("blockdevices", [])
        for dev in blockdevs:
            name = dev.get("name", "")
            lines.append(f"\n--- /dev/{name} ---")
            cmd = f"smartctl -j -a /dev/{name} 2>&1"
            if not security.is_command_safe(cmd):
                lines.append("  ❌ Command blocked by security")
                continue

            health = await _run_ssh(cmd)
            if "❌" in health:
                lines.append(f"  ❌ {health}")
                continue

            try:
                smart_data = json.loads(health)
                smart_device = parse_smart_json(smart_data)
                health_emoji = (
                    "🔴"
                    if smart_device.health_score < 50
                    else "🟡"
                    if smart_device.health_score < 80
                    else "✅"
                )
                lines.append(
                    f"  {health_emoji} Health: {smart_device.health_score}/100"
                )
                lines.append(f"  Model: {smart_device.model}")
                lines.append(f"  SMART: {smart_device.overall_health}")
                if smart_device.temperature > 0:
                    temp_warn = " 🔥" if smart_device.temperature > 55 else ""
                    lines.append(f"  Temp: {smart_device.temperature}°C{temp_warn}")
                if smart_device.warnings:
                    for w in smart_device.warnings[:3]:
                        lines.append(f"  ⚠️  {w}")
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                lines.append(f"  ⚠️  Parse error: {e}")
    except (json.JSONDecodeError, KeyError):
        lines.append("Could not parse disk list for health checks")

    return "\n".join(lines)


async def main():
    """Run the MCP server."""
    logger.info("Starting Disk Health MCP...")
    logger.info(
        "Available tools: list_disks, get_disk_health, get_smart_attributes, "
        "get_nvme_health, run_smart_test, get_zfs_status, get_raid_status, "
        "get_io_stats, query_prometheus_disk, query_influxdb_disk, "
        "get_full_disk_report"
    )
    await mcp.run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
