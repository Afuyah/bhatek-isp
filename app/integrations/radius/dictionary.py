"""
MikroTik RADIUS Dictionary
===========================
MikroTik vendor-specific RADIUS attribute formatting and parsing.

Vendor ID: 14988 (MikroTik)

Key Attributes Used:
    - Mikrotik-Rate-Limit (7): Bandwidth limits with burst support
    - Mikrotik-Total-Limit (40): Data cap in bytes
    - Mikrotik-Address-List (not vendor-specific, but critical): Firewall address list tagging
    - Session-Timeout (27): Maximum session duration in seconds
    - Idle-Timeout (28): Disconnect after idle period in seconds
    - Simultaneous-Use: Max concurrent sessions (device limit)

Rate-Limit Format: "TX/RX [burst-TX/burst-RX [threshold-TX/threshold-RX [time-TX/time-RX]]]"
    Example: "10M/5M 20M/10M 15M/15M 30s/30s"
    - 10M download, 5M upload (sustained)
    - 20M/10M burst
    - Burst when average < 15M
    - Burst for max 30 seconds
"""


class MikroTikDictionary:
    """MikroTik RADIUS vendor-specific attribute handler."""

    # MikroTik Vendor ID
    VENDOR_ID = 14988

    # MikroTik Vendor-Specific Attribute IDs
    ATTR_RATE_LIMIT = 7       # Rate limit string
    ATTR_TOTAL_LIMIT = 40     # Total data limit in bytes
    ATTR_BURST_LIMIT = 41     # Burst limit
    ATTR_BURST_THRESHOLD = 42 # Burst threshold
    ATTR_BURST_TIME = 43      # Burst time

    # Standard RADIUS attribute names used by MikroTik
    ATTR_SESSION_TIMEOUT = 'Session-Timeout'
    ATTR_IDLE_TIMEOUT = 'Idle-Timeout'
    ATTR_MIKROTIK_RATE_LIMIT = 'Mikrotik-Rate-Limit'
    ATTR_MIKROTIK_TOTAL_LIMIT = 'Mikrotik-Total-Limit'
    ATTR_MIKROTIK_ADDRESS_LIST = 'Mikrotik-Address-List'
    ATTR_MIKROTIK_GROUP = 'Mikrotik-Group'
    ATTR_FRAMED_POOL = 'Framed-Pool'
    ATTR_SIMULTANEOUS_USE = 'Simultaneous-Use'

    # =========================================================================
    # RATE LIMIT FORMATTER
    # =========================================================================

    @classmethod
    def format_rate_limit(
        cls,
        upload: int,
        download: int,
        upload_burst: int = None,
        download_burst: int = None,
        burst_threshold: int = None,
        burst_time: int = None,
        unit: str = "M",
    ) -> str:
        """
        Format a MikroTik rate-limit string.

        MikroTik format: "TX/RX [burst-TX/burst-RX [threshold [time]]]"

        Args:
            upload: Upload speed (applied as RX on MikroTik)
            download: Download speed (applied as TX on MikroTik)
            upload_burst: Burst upload speed
            download_burst: Burst download speed
            burst_threshold: Average speed threshold to trigger burst
            burst_time: Max burst duration in seconds
            unit: Speed unit (M, k, G) — default "M" for Mbps

        Returns:
            Formatted rate-limit string, e.g., "10M/5M"
        """
        rate_limit = f"{download}{unit}/{upload}{unit}"

        if upload_burst is not None and download_burst is not None:
            rate_limit += f" {download_burst}{unit}/{upload_burst}{unit}"

        if burst_threshold is not None:
            rate_limit += f" {burst_threshold}{unit}/{burst_threshold}{unit}"

        if burst_time is not None:
            rate_limit += f" {burst_time}s/{burst_time}s"

        return rate_limit

    # =========================================================================
    # RATE LIMIT PARSER
    # =========================================================================

    @classmethod
    def parse_rate_limit(cls, rate_limit_str: str) -> dict:
        """
        Parse a MikroTik rate-limit string back to structured data.

        Args:
            rate_limit_str: MikroTik rate-limit string

        Returns:
            Dict with upload, download, burst values (all integers where parsed)
        """
        def parse_value(value: str):
            """Parse a single value with optional unit suffix."""
            if not value:
                return None
            value = value.strip().lower()
            if value.endswith("m"):
                return int(value[:-1])
            if value.endswith("k"):
                return round(int(value[:-1]) / 1000, 1)
            if value.endswith("g"):
                return int(value[:-1]) * 1000
            if value.endswith("s"):
                return int(value[:-1])
            try:
                return int(value)
            except ValueError:
                return None

        parts = rate_limit_str.split()

        result = {
            "upload": None,
            "download": None,
            "upload_burst": None,
            "download_burst": None,
            "burst_threshold": None,
            "burst_time": None,
        }

        try:
            if len(parts) >= 1:
                d, u = parts[0].split("/")
                result["download"] = parse_value(d)
                result["upload"] = parse_value(u)

            if len(parts) >= 2 and "/" in parts[1]:
                d, u = parts[1].split("/")
                result["download_burst"] = parse_value(d)
                result["upload_burst"] = parse_value(u)

            if len(parts) >= 3 and "/" in parts[2]:
                threshold = parts[2].split("/")[0]
                result["burst_threshold"] = parse_value(threshold)

            if len(parts) >= 4 and "/" in parts[3]:
                time_val = parts[3].split("/")[0]
                result["burst_time"] = parse_value(time_val)
        except Exception:
            # Return partial results on parse failure
            pass

        return result

    # =========================================================================
    # STANDARD RADIUS ATTRIBUTES (FROM PLAN DICT)
    # =========================================================================

    @classmethod
    def get_radius_attributes(cls, plan: dict) -> dict:
        """
        Generate standard RADIUS reply attributes from a plan dict.

        Args:
            plan: Dict with plan fields:
                - session_timeout: Session timeout in seconds
                - idle_timeout: Idle timeout in seconds
                - bandwidth_up: Upload speed in Mbps
                - bandwidth_down: Download speed in Mbps
                - burst_up, burst_down, burst_threshold, burst_time: Burst settings
                - data_limit: Data cap in MB (converted to bytes)
                - pool_name: IP pool for Framed-Pool
                - profile: Profile name for Mikrotik-Group
                - address_list: Firewall address list name
                - device_limit: Max concurrent sessions for Simultaneous-Use

        Returns:
            Dict of RADIUS attribute name → value
        """
        attributes = {}

        # Session timeout
        if plan.get("session_timeout") is not None:
            attributes[cls.ATTR_SESSION_TIMEOUT] = int(plan["session_timeout"])

        # Idle timeout
        if plan.get("idle_timeout") is not None:
            attributes[cls.ATTR_IDLE_TIMEOUT] = int(plan["idle_timeout"])

        # Rate limit (bandwidth)
        upload = plan.get("bandwidth_up")
        download = plan.get("bandwidth_down")
        if upload is not None and download is not None:
            attributes[cls.ATTR_MIKROTIK_RATE_LIMIT] = cls.format_rate_limit(
                upload=upload,
                download=download,
                upload_burst=plan.get("burst_up"),
                download_burst=plan.get("burst_down"),
                burst_threshold=plan.get("burst_threshold"),
                burst_time=plan.get("burst_time"),
                unit=plan.get("unit", "M"),
            )

        # Data cap (convert MB to bytes for MikroTik)
        if plan.get("data_limit") is not None:
            data_limit_bytes = int(plan["data_limit"]) * 1024 * 1024
            attributes[cls.ATTR_MIKROTIK_TOTAL_LIMIT] = data_limit_bytes

        # IP Pool
        if plan.get("pool_name"):
            attributes[cls.ATTR_FRAMED_POOL] = plan["pool_name"]

        # Profile / Queue group
        if plan.get("profile"):
            attributes[cls.ATTR_MIKROTIK_GROUP] = plan["profile"]

        # Firewall address list (for marking paid users)
        if plan.get("address_list"):
            attributes[cls.ATTR_MIKROTIK_ADDRESS_LIST] = plan["address_list"]

        # Concurrent sessions (device limit)
        if plan.get("device_limit") is not None and plan["device_limit"] > 1:
            attributes[cls.ATTR_SIMULTANEOUS_USE] = str(plan["device_limit"])

        return attributes

    # =========================================================================
    # VENDOR-SPECIFIC FORMAT (for low-level RADIUS libraries)
    # =========================================================================

    @classmethod
    def get_vendor_attributes(cls, plan: dict) -> dict:
        """
        Return attributes in vendor-specific tuple format.

        Format: {(vendor_id, attribute_id): value}

        Used by low-level RADIUS libraries that need explicit
        vendor-attribute encoding.

        Args:
            plan: Dict with bandwidth_up, bandwidth_down, data_limit

        Returns:
            Dict of (vendor_id, attr_id) → value
        """
        attrs = {}

        upload = plan.get("bandwidth_up")
        download = plan.get("bandwidth_down")
        if upload and download:
            rate = cls.format_rate_limit(upload=upload, download=download)
            attrs[(cls.VENDOR_ID, cls.ATTR_RATE_LIMIT)] = rate

        if plan.get("data_limit"):
            data_bytes = int(plan["data_limit"]) * 1024 * 1024
            attrs[(cls.VENDOR_ID, cls.ATTR_TOTAL_LIMIT)] = data_bytes

        return attrs

    # =========================================================================
    # CONVENIENCE: BUILD ATTRIBUTES FROM SUBSCRIPTION
    # =========================================================================

    @classmethod
    def get_attributes_from_subscription(cls, subscription, plan=None) -> dict:
        """
        Generate RADIUS reply attributes from Subscription and Plan models.

        This is the primary method used by the auth handler and sync service.
        It prefers subscription overrides over plan defaults.

        Args:
            subscription: Subscription ORM instance
            plan: Plan ORM instance (uses subscription.plan if not provided)

        Returns:
            Dict of RADIUS attribute name → value
        """
        plan_obj = plan or subscription.plan
        if not plan_obj:
            return {}

        attributes = {}

        # Session timeout (plan default or 24 hours)
        session_timeout = plan_obj.session_timeout_seconds or 86400
        attributes[cls.ATTR_SESSION_TIMEOUT] = session_timeout

        # Idle timeout (plan default or 5 minutes)
        idle_timeout = plan_obj.idle_timeout_seconds or 300
        attributes[cls.ATTR_IDLE_TIMEOUT] = idle_timeout

        # Bandwidth (prefer subscription overrides)
        bw_up = subscription.bandwidth_up_mbps or plan_obj.bandwidth_up_mbps or 0
        bw_down = subscription.bandwidth_down_mbps or plan_obj.bandwidth_down_mbps or 0

        if bw_up > 0 or bw_down > 0:
            # If only one direction set, use same value for both
            actual_up = bw_up if bw_up > 0 else bw_down
            actual_down = bw_down if bw_down > 0 else bw_up
            attributes[cls.ATTR_MIKROTIK_RATE_LIMIT] = cls.format_rate_limit(
                upload=actual_up,
                download=actual_down,
                unit="M",
            )

        # Data cap
        if plan_obj.validity_type == 'data_based' and plan_obj.data_limit_mb:
            total_bytes = int(plan_obj.data_limit_mb) * 1024 * 1024
            attributes[cls.ATTR_MIKROTIK_TOTAL_LIMIT] = total_bytes

        # Device limit
        device_limit = subscription.get_device_limit()
        if device_limit > 1:
            attributes[cls.ATTR_SIMULTANEOUS_USE] = str(device_limit)

        # Address list for marking paid users (enables firewall-based access control)
        attributes[cls.ATTR_MIKROTIK_ADDRESS_LIST] = 'paid-users'

        return attributes