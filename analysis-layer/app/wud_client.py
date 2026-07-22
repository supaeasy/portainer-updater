import httpx

from . import config


async def get_containers() -> list[dict]:
    """Holt den aktuellen Zustand aller von WUD ueberwachten Container.

    Schema (siehe getwud/wud app/model/container.ts):
      name, image.name, image.tag.value, updateAvailable,
      updateKind.kind ('tag'|'digest'|'unknown'),
      updateKind.localValue, updateKind.remoteValue
    """
    url = f"{config.WUD_API_URL}/api/containers"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def extract_update_info(container: dict) -> dict | None:
    """Normalisiert ein WUD-Container-Objekt (API oder Webhook-Payload) auf
    die Felder, die wir fuer die Analyse brauchen. Gibt None zurueck, wenn
    kein Update verfuegbar ist."""
    if not container.get("updateAvailable"):
        return None

    update_kind = container.get("updateKind") or {}
    image = container.get("image") or {}
    result = container.get("result") or {}

    kind = update_kind.get("kind", "unknown")
    current_version = update_kind.get("localValue") or image.get("tag", {}).get("value")
    new_version = update_kind.get("remoteValue") or result.get("tag")

    return {
        "container": container.get("name"),
        "image_name": image.get("name"),
        "kind": kind,
        "current_version": current_version,
        "new_version": new_version,
        # Fuer Digest-only-Updates (z.B. :latest): kein Versions-Tag vorhanden,
        # aber Erstellungsdatum + ggf. OCI-Version-Label helfen bei der Annaeherung.
        "image_created": image.get("created"),
        "labels": container.get("labels") or {},
    }
