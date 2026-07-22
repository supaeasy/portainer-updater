import logging

from . import analyzer, config, github_client, portainer_client, storage, wud_client

log = logging.getLogger("pipeline")

# Gaengige OCI-/Label-Schema-Keys, in denen Images ihre eigene Version angeben.
# Wenn eines davon gesetzt ist, ist das exakt statt geschaetzt.
_VERSION_LABEL_KEYS = (
    "org.opencontainers.image.version",
    "org.label-schema.version",
    "version",
)


async def _resolve_versions_for_untagged_update(update_info: dict, github_repo: str):
    """Versucht fuer ein Update ohne Versions-Tag (z.B. :latest, per Digest
    erkannt) trotzdem eine Versions-Spanne zu ermitteln, damit die gleiche
    Changelog-/Breaking-Change-Analyse laufen kann wie bei getaggten Images:

    - aktuelle Version: bevorzugt aus einem OCI-Versions-Label des Containers
      (exakt), sonst genaehert ueber das naechstliegende GitHub-Release vor
      dem Erstellungsdatum des lokal laufenden Images.
    - neue Version: aktuellstes GitHub-Release des Repos.

    Gibt (current_tag, new_tag, hinweistext) zurueck, oder None, wenn sich
    nichts Belastbares ermitteln laesst (z.B. kein Label, kein Erstellungs-
    datum, kein GitHub-Release gefunden)."""
    labels = update_info.get("labels") or {}
    current_tag = None
    notes = []

    for key in _VERSION_LABEL_KEYS:
        if labels.get(key):
            current_tag = labels[key]
            notes.append(
                f"Aktuelle Version exakt aus Image-Label '{key}' uebernommen "
                f"(Container hat keinen Versions-Tag, z.B. :latest)."
            )
            break

    if not current_tag:
        image_created = update_info.get("image_created")
        if not image_created:
            return None
        try:
            current_release = await github_client.get_release_at_or_before(github_repo, image_created)
        except Exception:  # noqa: BLE001
            current_release = None
        if not current_release:
            return None
        current_tag = current_release.get("tag_name")
        notes.append(
            f"Kein Versions-Tag im Container (z.B. :latest) - aktuelle Version daher "
            f"NUR GESCHAETZT anhand des Image-Erstellungsdatums ({image_created}): "
            f"naechstliegendes GitHub-Release davor war {current_tag}."
        )

    try:
        latest_release = await github_client.get_latest_release(github_repo)
    except Exception:  # noqa: BLE001
        latest_release = None
    if not latest_release:
        return None

    new_tag = latest_release.get("tag_name")
    if new_tag == current_tag:
        # Kein Versions-Sprung erkennbar -> nichts Sinnvolles zu analysieren.
        return None

    notes.append(
        "Neue Version = aktuellstes GitHub-Release des Repos. Hinweis: ':latest' "
        "im Container verfolgt nicht zwingend jedes GitHub-Release 1:1 - bei "
        "Abweichungen bitte das tatsaechlich gepullte Image manuell gegenchecken."
    )

    return current_tag, new_tag, " ".join(notes)


async def process_update(update_info: dict, stacks_config: dict):
    """Nimmt ein normalisiertes Update (container, image_name, kind,
    current_version, new_version, ggf. image_created/labels) entgegen, legt
    einen DB-Eintrag an und stoesst - falls moeglich - die Changelog-/
    Breaking-Change-Analyse an."""
    container = update_info.get("container")
    current_version = update_info.get("current_version")
    new_version = update_info.get("new_version")
    kind = update_info.get("kind")
    image_name = update_info.get("image_name")

    if not container or new_version is None:
        return

    cfg = stacks_config.get(container)
    stack_name = cfg["portainer_stack_name"] if cfg else None

    version_note = update_info.get("version_note", "")
    analyzable = kind == "tag"

    if kind != "tag" and cfg and cfg.get("github_repo"):
        resolved = await _resolve_versions_for_untagged_update(update_info, cfg["github_repo"])
        if resolved:
            current_version, new_version, version_note = resolved
            analyzable = True

    existing = storage.get_existing(container, current_version, new_version)
    storage.upsert_pending(container, stack_name, image_name, current_version, new_version, kind, version_note)

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

    if not cfg.get("github_repo"):
        storage.save_analysis(container, current_version, new_version, {
            "risk": "unknown",
            "summary": (
                "Update erkannt, aber kein github_repo in stacks.yml fuer diesen "
                "Container hinterlegt - keine automatische Changelog-Analyse "
                "moeglich. Bitte Release-Notes manuell pruefen."
            ),
            "compose_change_needed": False,
            "compose_change_explanation": "",
            "compose_patch": "",
            "raw": "",
        })
        return

    if not analyzable:
        storage.save_analysis(container, current_version, new_version, {
            "risk": "unknown",
            "summary": (
                "Update erkannt (Digest-Aenderung, kein Versions-Tag), aber weder "
                "ein Versions-Label im Image noch ein passendes GitHub-Release zum "
                "Erstellungsdatum gefunden - keine Versions-Naeherung moeglich. "
                "Bitte manuell pruefen, welche Version das Image tatsaechlich hat."
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
            version_note=version_note,
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
