import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, discovery, pipeline, portainer_client, storage, wud_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("main")

_stacks_config = {}
_poll_task = None


async def _poll_loop():
    interval = max(config.ANALYSIS_POLL_INTERVAL_MINUTES, 5) * 60
    while True:
        try:
            await pipeline.rescan_all(_stacks_config)
        except Exception:  # noqa: BLE001
            log.exception("Periodischer Rescan fehlgeschlagen")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _stacks_config, _poll_task
    storage.init_db()
    _stacks_config = config.load_stacks_config()
    _poll_task = asyncio.create_task(_poll_loop())
    yield
    if _poll_task:
        _poll_task.cancel()


app = FastAPI(title="Docker Update Dashboard", lifespan=lifespan)


@app.post("/webhook/wud")
async def webhook_wud(payload: dict):
    """Wird von WUD's http-Trigger aufgerufen, sobald ein Update erkannt wurde."""
    info = wud_client.extract_update_info(payload)
    if info:
        await pipeline.process_update(info, _stacks_config)
    return {"ok": True}


@app.post("/api/rescan")
async def api_rescan():
    await pipeline.rescan_all(_stacks_config)
    return {"ok": True}


@app.post("/api/discover-stacks")
async def api_discover_stacks():
    """Liest alle Portainer-Stacks/-Container aus und generiert daraus eine
    (mit der aktuell geladenen stacks.yml gemergte) Vorschlagsdatei. Schreibt
    NICHT direkt in stacks.yml (die ist read-only gemountet) - Ergebnis landet
    zusaetzlich unter data/stacks.discovered.yml, review + Uebernahme von Hand."""
    if not config.PORTAINER_URL or not config.PORTAINER_API_KEY:
        raise HTTPException(400, "PORTAINER_URL/PORTAINER_API_KEY nicht konfiguriert")

    try:
        result = await discovery.discover(_stacks_config)
    except Exception as exc:  # noqa: BLE001
        log.exception("Discovery fehlgeschlagen")
        raise HTTPException(502, f"Portainer-Abfrage fehlgeschlagen: {exc}") from None

    yaml_text = discovery.render_yaml(result["entries"])
    out_path = os.path.join(os.path.dirname(config.DB_PATH), "stacks.discovered.yml")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(yaml_text)

    return {
        "yaml": yaml_text,
        "written_to": out_path,
        "added": result["added"],
        "updated": result["updated"],
        "auto_repo": result["auto_repo"],
        "needs_repo": result["needs_repo"],
        "missing": result["missing"],
        "errors": result["errors"],
    }


@app.post("/api/reload-config")
async def api_reload_config():
    """Laedt stacks.yml neu ein, ohne den Container neu starten zu muessen
    (z.B. nachdem eine mit /api/discover-stacks erzeugte Datei uebernommen wurde)."""
    global _stacks_config
    _stacks_config = config.load_stacks_config()
    return {"ok": True, "containers": sorted(_stacks_config.keys())}


@app.post("/api/analyze/{container}")
async def api_reanalyze(container: str):
    """Erzwingt eine erneute Analyse (z.B. nachdraeglich stacks.yml ergaenzt)."""
    row = storage.get_latest_pending(container)
    if not row:
        raise HTTPException(404, "Kein offenes Update fuer diesen Container gefunden")
    # Analyse zuruecksetzen, damit process_update sie neu erstellt
    storage.save_analysis(container, row["current_version"], row["new_version"], {
        "risk": None, "summary": None, "compose_change_needed": False,
        "compose_change_explanation": None, "compose_patch": None, "raw": None,
    })
    info = {
        "container": container,
        "image_name": row["image_name"],
        # "tag" erzwingen: current/new_version sind hier bereits aufgeloeste
        # Versions-Strings (auch bei urspruenglich per Digest erkannten
        # Updates - siehe pipeline._resolve_versions_for_untagged_update).
        # Ein erneuter Resolve-Versuch wuerde ohne labels/image_created scheitern.
        "kind": "tag",
        "current_version": row["current_version"],
        "new_version": row["new_version"],
        "version_note": row["version_note"] or "",
    }
    await pipeline.process_update(info, _stacks_config)
    return {"ok": True}


@app.get("/api/status")
async def api_status():
    rows = storage.list_all()
    for r in rows:
        cfg = _stacks_config.get(r["container"])
        r["configured"] = cfg is not None
        r["notes"] = cfg.get("notes", "") if cfg else ""
    return rows


class ApplyItem(BaseModel):
    container: str
    apply_compose_patch: bool = False


class ApplyRequest(BaseModel):
    items: list[ApplyItem]


@app.post("/api/apply")
async def api_apply(req: ApplyRequest):
    results = []
    for item in req.items:
        row = storage.get_latest_pending(item.container)
        cfg = _stacks_config.get(item.container)
        if not row or not cfg:
            results.append({"container": item.container, "ok": False, "error": "Kein offener Eintrag / keine stacks.yml-Konfiguration"})
            continue

        try:
            stack = await portainer_client.find_stack_by_name(cfg["portainer_stack_name"])
            if not stack:
                raise RuntimeError(f"Stack '{cfg['portainer_stack_name']}' nicht in Portainer gefunden")

            stack_detail = await portainer_client.get_stack(stack["Id"])
            env = stack_detail.get("Env", [])

            if item.apply_compose_patch and row.get("compose_patch"):
                content = row["compose_patch"]
            else:
                content = await portainer_client.get_stack_file(stack["Id"])

            await portainer_client.update_stack(
                stack_id=stack["Id"],
                endpoint_id=cfg["portainer_endpoint_id"] or stack.get("EndpointId"),
                stack_file_content=content,
                env=env,
                repull_and_redeploy=True,
            )
            storage.set_status(item.container, row["current_version"], row["new_version"], "applied")
            results.append({"container": item.container, "ok": True})
        except Exception as exc:  # noqa: BLE001
            log.exception("Update fehlgeschlagen fuer %s", item.container)
            results.append({"container": item.container, "ok": False, "error": str(exc)})

    return {"results": results}


class DismissRequest(BaseModel):
    container: str


@app.post("/api/dismiss")
async def api_dismiss(req: DismissRequest):
    row = storage.get_latest_pending(req.container)
    if not row:
        raise HTTPException(404, "Kein offenes Update gefunden")
    storage.set_status(req.container, row["current_version"], row["new_version"], "dismissed")
    return {"ok": True}


app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def index():
    return FileResponse("app/static/index.html")
