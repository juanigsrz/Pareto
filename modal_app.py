import os

import modal

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


# --- Modal application -------------------------------------------------------

image = (modal.Image.debian_slim()
         .pip_install("gurobipy==13.0.2", "fastapi[standard]")
         .add_local_python_source("pareto_core", "serialize"))

app = modal.App("pareto", image=image)

THREADS = int(os.environ.get("PARETO_THREADS", "8"))


def _gurobi_env():
    """Build a Gurobi WLS Env from the gurobi-wls Modal Secret (banner off)."""
    import gurobipy as gp
    return gp.Env(params={
        "WLSACCESSID": os.environ["WLSACCESSID"],
        "WLSSECRET": os.environ["WLSSECRET"],
        "LICENSEID": int(os.environ["LICENSEID"]),
        "OutputFlag": 0,
    })


@app.function(cpu=8, timeout=900,
              secrets=[modal.Secret.from_name("gurobi-wls")])
def solve_job(req):
    """Worker: req is the validated dict from validate_request()."""
    import serialize as S
    try:
        env = _gurobi_env()
        res = C.solve(
            req["instance"], kpi=req["kpi"],
            time_limit=req["time_limit"], mipgap=req["mipgap"],
            env=env, threads=THREADS, want_stats=req["want_stats"],
        )
        return S.to_dict(res)
    except ValueError as e:               # parse / build errors
        return {"status": "error", "error": str(e)}


@app.function()
@modal.asgi_app(requires_proxy_auth=True)
def web():
    from fastapi import FastAPI, HTTPException, Request

    api = FastAPI(title="Pareto")

    @api.post("/solve", status_code=202)
    async def submit(request: Request):
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(400, "body must be valid JSON")
        try:
            req = validate_request(payload)
        except ValidationError as e:
            raise HTTPException(400, str(e))
        call = solve_job.spawn(req)
        return {"job_id": call.object_id}

    @api.get("/result/{job_id}")
    async def result(job_id: str):
        try:
            fc = modal.FunctionCall.from_id(job_id)
            out = fc.get(timeout=0)
        except TimeoutError:
            return {"status": "pending"}
        except modal.exception.NotFoundError:
            raise HTTPException(404, "unknown job_id")
        return {"status": "done", "result": out}

    return api
