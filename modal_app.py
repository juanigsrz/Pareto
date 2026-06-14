import os
import pareto_core as C

MAX_INSTANCE_BYTES = int(os.environ.get("PARETO_MAX_INSTANCE_BYTES",
                                        str(1024 * 1024)))   # 1 MiB
MAX_TIME_LIMIT = float(os.environ.get("PARETO_MAX_TIME_LIMIT", "600"))


class ValidationError(Exception):
    """Raised for a malformed POST /solve payload (-> HTTP 400)."""


def validate_request(payload):
    """Validate + normalize a /solve JSON body. Returns a clean dict
    {instance, kpi, time_limit, mipgap, want_stats} or raises ValidationError.
    """
    if not isinstance(payload, dict):
        raise ValidationError("body must be a JSON object")

    instance = payload.get("instance")
    if not isinstance(instance, str) or not instance.strip():
        raise ValidationError("'instance' is required and must be non-empty")
    if len(instance.encode("utf-8")) > MAX_INSTANCE_BYTES:
        raise ValidationError(f"'instance' too large (max {MAX_INSTANCE_BYTES} bytes)")

    kpi_raw = payload.get("kpi", "trades")
    if isinstance(kpi_raw, list):
        kpi_raw = ",".join(kpi_raw)
    if not isinstance(kpi_raw, str):
        raise ValidationError("'kpi' must be a string or list of strings")
    try:
        kpi = C.parse_kpi_list(kpi_raw)
    except Exception as e:
        raise ValidationError(f"invalid 'kpi': {e}")

    time_limit = payload.get("time_limit")
    if time_limit is not None:
        if not isinstance(time_limit, (int, float)) or isinstance(time_limit, bool):
            raise ValidationError("'time_limit' must be a number")
        if not (0 < time_limit <= MAX_TIME_LIMIT):
            raise ValidationError(
                f"'time_limit' must be in (0, {MAX_TIME_LIMIT}]")

    mipgap = payload.get("mipgap")
    if mipgap is not None:
        if not isinstance(mipgap, (int, float)) or isinstance(mipgap, bool):
            raise ValidationError("'mipgap' must be a number")
        if mipgap < 0:
            raise ValidationError("'mipgap' must be >= 0")

    return {
        "instance": instance,
        "kpi": kpi,
        "time_limit": time_limit,
        "mipgap": mipgap,
        "want_stats": bool(payload.get("stats", False)),
    }
