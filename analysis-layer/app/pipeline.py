import logging

from . import analyzer, config, github_client, portainer_client, storage, wud_client

log = logging.getLogger("pipeline")


async def process_update(update_info: dict, stacks_config: dict):
    """Nimmt ein normalisiertes Update (container, image_name, kind,
    current_version, new_version) entgegen, legt einen DB-Eintrag an und
    stoesst - falls moeglich - die Changelog-/Breaking-Change-Analyse an."""
    container = update_info.get("container")
    current_version = update_info.get("current_version")
    new_version = update_info.get("new_version")
    kind = update_info.get("kind")
    image_name = update_info.get("image_name")

    if not container or new_version is None:
        return

    cfg = stacks_config.get(container)
    stack_name = cfg["portainer_stack_name"] if cfg else None

    existing = storage.get_existing(container, current_version, new_version)
    storage.upsert_pending(container, stack_name, image_name, current_version, new_version, kind)

    if existing and existing.get("summary"):
        # Diese Versions-Kombination wurde schon analysiert - keine erneute
        # Claude-Anfrage noetig (spart Kosten bei wiederholten Webhook-/Poll-Treffern).
        return

    if not cfg:
        storage.save_analysis(container, current_version, new_version, {
            "risk": "unknown",
            "summary": (
                "Kein Eintrag in stacks.yml fuer diesen Container gefunden - "
                "es kann kein Portainer-Stack und kein GitHub-Repo zugeordnet werden. "
                "Bitte stacks.yml ergaenzen, wenn dieser Container ueber das Dashboard "
                "verwaltet werden soll."
            ),
            "compose_change_needed": False,
            "compose_change_explanation": "",
            "compose_patch": "",
            "raw": "",
        })
        return

    if kind != "tag" or not cfg.get("github_repo"):
        storage.save_analysis(container, current_version, new_version, {
            "risk": "unknown",
            "summary": (
                "Update erkannt, aber keine Versions-Tags vergleichbar (Digest-Update) "
                "oder kein github_repo in stacks.yml hinterlegt - keine automatische "
                "Changelog-Analyse moeglich. Bitte Release-Notes manuell pruefen."
            ),
            "compose_change_needed": False,
            "compose_change_explanation": "",
            "compose_patch": "",
            "raw": "",
        })
        return

    try:
        release_notes = await github_client.get_releases_between(
            cfg["github_repo"], current_version, new_version
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("GitHub-Abfrage fehlgeschlagen fuer %s", container)
        release_notes = f"[Fehler beim Abruf der GitHub-Releases: {exc}]"

    compose_content = ""
    if config.PORTAINER_URL and config.PORTAINER_API_KEY:
        try:
            stack = await portainer_client.find_stack_by_name(stack_name)
            if stack:
                compose_content = await portainer_client.get_stack_file(stack["Id"])
        except Exception as exc:  # noqa: BLE001
            log.exception("Portainer-Abfrage fehlgeschlagen fuer Stack %s", stack_name)
            compose_content = f"[Fehler beim Abruf der compose-Datei von Portainer: {exc}]"

    try:
        analysis = await analyzer.analyze_update(
            container=container,
            image_name=image_name,
            current_version=current_version,
            new_version=new_version,
            release_notes=release_notes,
            compose_content=compose_content,
            extra_notes=cfg.get("notes", ""),
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("Claude-Analyse fehlgeschlagen fuer %s", container)
        analysis = {
            "risk": "unknown",
            "summary": f"Analyse fehlgeschlagen: {exc}",
            "compose_change_needed": False,
            "compose_change_explanation": "",
            "compose_patch": "",
            "raw": "",
        }

    storage.save_analysis(container, current_version, new_version, analysis)


async def rescan_all(stacks_config: dict):
    """Fragt WUD nach allen Containern und stoesst fuer jeden mit verfuegbarem
    Update die Verarbeitung an (Sicherheitsnetz zusaetzlich zum Webhook)."""
    containers = await wud_client.get_containers()
    for c in containers:
        info = wud_client.extract_update_info(c)
        if info:
            await process_update(info, stacks_config)
