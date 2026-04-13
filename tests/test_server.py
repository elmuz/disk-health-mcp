"""
Tests for server.py tool handlers.

Uses mocked SSH, security, and config to test
the actual tool logic without a real host.
"""

from disk_health_mcp.server import (
    _enrich_error_output,
    _format_influxdb_device_health,
)

# ============================================================================
# Error Enrichment
# ============================================================================


class TestEnrichErrorOutput:
    """Verify error hinting works in isolation."""

    def test_permission_denied(self):
        result = _enrich_error_output("smartctl: /dev/sda: Permission denied")
        assert "require root privileges" in result

    def test_command_not_found(self):
        result = _enrich_error_output("bash: smartctl: command not found")
        assert "Hint" in result

    def test_clean_output_passthrough(self):
        clean = "smartctl 7.4, JSON output follows"
        assert _enrich_error_output(clean) == clean


# ============================================================================
# InfluxDB Health Formatting
# ============================================================================


class TestInfluxDBHealthFormatting:
    """Verify _format_influxdb_device_health renders correctly."""

    def test_healthy_sata_drive(self):
        row = {
            "device": "sda",
            "model": "ST18000NT001-3NF101",
            "serial_no": "ZVTFE2SL",
            "health_ok": True,
            "temp_c": 33,
            "power_on_hours": 10508,
            "power_cycle_count": 133,
        }
        result = _format_influxdb_device_health(row)
        assert "/dev/sda" in result
        assert "ST18000NT001" in result
        assert "PASSED" in result
        assert "33°C" in result
        assert "10,508" in result
        assert "🔥" not in result

    def test_healthy_nvme_drive(self):
        row = {
            "device": "nvme0",
            "model": "CT1000P2SSD8",
            "serial_no": "2146E5E54409",
            "health_ok": True,
            "temp_c": 33,
            "power_on_hours": 32881,
            "power_cycle_count": 210,
            "percentage_used": 7,
            "critical_warning": 0,
            "media_errors": 0,
            "error_log_entries": 556,
            "available_spare": 100,
            "unsafe_shutdowns": 128,
        }
        result = _format_influxdb_device_health(row)
        assert "NVMe Life Used: 7%" in result
        assert "Error Log Entries: 556" in result
        assert "near end of life" not in result

    def test_nvme_near_end_of_life(self):
        row = {
            "device": "nvme0",
            "model": "CT1000P2SSD8",
            "serial_no": "2146E5E54409",
            "health_ok": True,
            "temp_c": 33,
            "power_on_hours": 32881,
            "power_cycle_count": 210,
            "percentage_used": 92,
            "critical_warning": 0,
            "media_errors": 0,
            "error_log_entries": 0,
            "available_spare": 100,
            "unsafe_shutdowns": 128,
        }
        result = _format_influxdb_device_health(row)
        assert "near end of life" in result

    def test_high_temperature_warning(self):
        row = {
            "device": "sda",
            "model": "ST18000NT001-3NF101",
            "serial_no": "ZVTFE2SL",
            "health_ok": True,
            "temp_c": 60,
            "power_on_hours": 10508,
            "power_cycle_count": 133,
        }
        result = _format_influxdb_device_health(row)
        assert "🔥" in result

    def test_failed_health(self):
        row = {
            "device": "sdb",
            "model": "ST18000NT001-3NF101",
            "serial_no": "ZVTFF270",
            "health_ok": False,
            "temp_c": 35,
            "power_on_hours": 10509,
            "power_cycle_count": 131,
        }
        result = _format_influxdb_device_health(row)
        assert "FAILED" in result


# ============================================================================
# Tool Handler Tests (with mocked infrastructure)
# ============================================================================


class TestGetDiskHealth:
    """Test get_disk_health with mocked backend."""

    async def test_valid_device(self, patched_server):
        """Should return health report for valid device."""
        from disk_health_mcp.server import get_disk_health

        result = await get_disk_health("sda")
        assert "sda" in result

    async def test_invalid_device(self, patched_server, mock_security):
        """Should reject invalid device names."""
        mock_security.validate_device_name.return_value = False
        from disk_health_mcp.server import get_disk_health

        result = await get_disk_health("sda;rm -rf /")
        assert "Invalid device name" in result


class TestListDisks:
    """Test list_disks with mocked backend."""

    async def test_returns_json(self, patched_server):
        """Should return disk list as JSON."""
        from disk_health_mcp.server import list_disks

        result = await list_disks()
        assert "blockdevices" in result
