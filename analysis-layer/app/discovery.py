import copy
import re

import yaml

from . import portainer_client

# Zeigt meist auf das GitHub-Repo des Images, z.B.
# "https://github.com/immich-app/immich" oder "...immich.git".
_SOURCE_LABEL_RE = re.compile(r"github\.com[:/]+([^/]+)/([^/.]+?)(?:\.git)?/?$")


def _parse_github_repo(source_url: str) -> str:
    if not source_url:
        return ""
    match = _SOURCE_LABEL_RE.search(source_url.strip())
    if not match:
        return ""
    return f"{match.group(1)}/{match.group(2)}"


async def discover(existing_entries: dict) -> dict:
    """Liest alle Portainer-Stacks und ihre Container aus und baut daraus
    stacks.yml-Eintraege. existing_entries (aus config.load_stacks_config())
    wird respektiert: bereits gesetzte github_repo/notes bleiben erhalten,
    nur Struktur-Fakten (Stackname, Environment-ID) werden aktualisiert und
    neue Container ergaenzt. Container, die frueher konfiguriert waren aber
    jetzt nicht mehr gefunden werden, bleiben unveraendert stehen (werden nur
    im 'missing'-Feld gemeldet) - nichts wird geloescht."""
    # Deepcopy: existing_entries ist die live genutzte stacks.yml-Config der
    # laufenden App - hier NIE in-place mutieren.
    merged = copy.deepcopy(existing_entries)

    stacks = await portainer_client.list_stacks()
    stack_names_by_endpoint: dict[int, dict[str, dict]] = {}
    for s in stacks:
        stack_names_by_endpoint.setdefault(s["EndpointId"], {})[s["Name"]] = s

    seen_containers = set()
    added, updated, auto_repo, needs_repo, errors = [], [], [], [], []

    for endpoint_id, stack_names in stack_names_by_endpoint.items():
        try:
            containers = await portainer_client.list_containers(endpoint_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Environment {endpoint_id}: {exc}")
            continue

        for c in containers:
            labels = c.get("Labels") or {}
            project = labels.get("com.docker.compose.project")
            if not project or project not in stack_names:
                continue  # kein bekannter Portainer-Stack (z.B. Standalone-Container)

            names = c.get("Names") or []
            if not names:
                continue
            container_name = names[0].lstrip("/")
            seen_containers.add(container_name)

            detected_repo = _parse_github_repo(labels.get("org.opencontainers.image.source", ""))

            existing = merged.get(container_name)
            if existing:
                structurally_changed = (
                    existing.get("portainer_stack_name") != project
                    or existing.get("portainer_endpoint_id") != endpoint_id
                )
                existing["portainer_stack_name"] = project
                existing["portainer_endpoint_id"] = endpoint_id
                if not existing.get("github_repo") and detected_repo:
                    existing["github_repo"] = detected_repo
                    auto_repo.append(container_name)
                if structurally_changed:
                    updated.append(container_name)
            else:
                merged[container_name] = {
                    "container": container_name,
                    "portainer_stack_name": project,
                    "portainer_endpoint_id": endpoint_id,
                    "github_repo": detected_repo,
                    "notes": "",
                }
                added.append(container_name)
                if detected_repo:
                    auto_repo.append(container_name)

            if not merged[container_name].get("github_repo"):
                needs_repo.append(container_name)

    missing = sorted(c for c in existing_entries if c not in seen_containers)

    return {
        "entries": merged,
        "added": sorted(added),
        "updated": sorted(updated),
        "auto_repo": sorted(set(auto_repo)),
        "needs_repo": sorted(set(needs_repo)),
        "missing": missing,
        "errors": errors,
    }


def render_yaml(entries: dict) -> str:
    ordered = sorted(entries.values(), key=lambda e: e["container"])
    body = yaml.safe_dump(
        {"stacks": ordered}, default_flow_style=False, sort_keys=False, allow_unicode=True
    )

    lines = []
    for line in body.splitlines():
        if re.match(r"^\s*github_repo:\s*(''|\"\")\s*$", line):
            line += "  # TODO: nicht automatisch erkannt (kein org.opencontainers.image.source-Label) - bitte ergaenzen"
        lines.append(line)

    header = (
        "# Automatisch generiert/ergaenzt ueber das Dashboard (\"Stacks entdecken\").\n"
        "# github_repo-Zeilen mit TODO-Kommentar bitte manuell pruefen/ergaenzen,\n"
        "# dann diese Datei nach stacks.yml kopieren und den Container neu starten.\n"
    )
    return header + "\n".join(lines) + "\n"
