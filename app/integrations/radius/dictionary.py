class MikroTikDictionary:

    VENDOR_ID = 14988

    # MikroTik Vendor Attribute IDs
    ATTR_RATE_LIMIT = 7
    ATTR_TOTAL_LIMIT = 40
    ATTR_BURST_LIMIT = 41
    ATTR_BURST_THRESHOLD = 42
    ATTR_BURST_TIME = 43

    # RATE LIMIT FORMATTER
    @classmethod
    def format_rate_limit(
        cls,
        upload: int,
        download: int,
        upload_burst: int = None,
        download_burst: int = None,
        burst_threshold: int = None,
        burst_time: int = None,
        unit: str = "M"
    ) -> str:
        

        rate_limit = f"{download}{unit}/{upload}{unit}"

        if upload_burst is not None and download_burst is not None:
            rate_limit += f" {download_burst}{unit}/{upload_burst}{unit}"

        if burst_threshold is not None:
            rate_limit += f" {burst_threshold}{unit}/{burst_threshold}{unit}"

        if burst_time is not None:
            rate_limit += f" {burst_time}s/{burst_time}s"

        return rate_limit

    # RATE LIMIT PARSER
    @classmethod
    def parse_rate_limit(cls, rate_limit_str: str) -> dict:
      
        def parse_value(value: str):
            if not value:
                return None
            value = value.strip().lower()
            if value.endswith("m"):
                return int(value[:-1])
            if value.endswith("k"):
                return int(value[:-1]) / 1000
            if value.endswith("s"):
                return int(value[:-1])
            return int(value)

        parts = rate_limit_str.split()

        result = {
            "upload": None,
            "download": None,
            "upload_burst": None,
            "download_burst": None,
            "burst_threshold": None,
            "burst_time": None
        }

        try:
            if len(parts) >= 1:
                d, u = parts[0].split("/")
                result["download"] = parse_value(d)
                result["upload"] = parse_value(u)

            if len(parts) >= 2:
                d, u = parts[1].split("/")
                result["download_burst"] = parse_value(d)
                result["upload_burst"] = parse_value(u)

            if len(parts) >= 3:
                threshold = parts[2].split("/")[0]
                result["burst_threshold"] = parse_value(threshold)

            if len(parts) >= 4:
                time = parts[3].split("/")[0]
                result["burst_time"] = parse_value(time)

        except Exception:
            # Fail-safe: return partial parsed values instead of crashing
            pass

        return result

    # STANDARD RADIUS ATTRIBUTES
    @classmethod
    def get_radius_attributes(cls, plan: dict) -> dict:
        """
        Generate standard RADIUS reply attributes for MikroTik
        """

        attributes = {}

        # Session timeout (seconds)
        if plan.get("session_timeout") is not None:
            attributes["Session-Timeout"] = int(plan["session_timeout"])

        # Idle timeout (seconds)
        if plan.get("idle_timeout") is not None:
            attributes["Idle-Timeout"] = int(plan["idle_timeout"])

        # Rate limit
        if plan.get("bandwidth_up") is not None and plan.get("bandwidth_down") is not None:
            attributes["Mikrotik-Rate-Limit"] = cls.format_rate_limit(
                upload=plan["bandwidth_up"],
                download=plan["bandwidth_down"],
                upload_burst=plan.get("burst_up"),
                download_burst=plan.get("burst_down"),
                burst_threshold=plan.get("burst_threshold"),
                burst_time=plan.get("burst_time"),
                unit=plan.get("unit", "M")
            )

        # Data cap (bytes)
        if plan.get("data_limit") is not None:
            attributes["Mikrotik-Total-Limit"] = int(plan["data_limit"])

        # IP Pool assignment
        if plan.get("pool_name"):
            attributes["Framed-Pool"] = plan["pool_name"]

        # Router profile / queue group
        if plan.get("profile"):
            attributes["Mikrotik-Group"] = plan["profile"]

        return attributes

    # VENDOR-SPECIFIC FORMAT (ADVANCED)
    @classmethod
    def get_vendor_attributes(cls, plan: dict) -> dict:
        """
        Return attributes using Vendor-Specific format:
        (vendor_id, attribute_id): value

        Useful for low-level RADIUS libraries
        """

        attrs = {}

        if plan.get("bandwidth_up") and plan.get("bandwidth_down"):
            rate = cls.format_rate_limit(
                upload=plan["bandwidth_up"],
                download=plan["bandwidth_down"]
            )
            attrs[(cls.VENDOR_ID, cls.ATTR_RATE_LIMIT)] = rate

        if plan.get("data_limit"):
            attrs[(cls.VENDOR_ID, cls.ATTR_TOTAL_LIMIT)] = int(plan["data_limit"])

        return attrs