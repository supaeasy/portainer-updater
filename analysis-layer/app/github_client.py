import re

import httpx

from . import config


def _parse_version(tag: str):
    """Sehr einfacher Versions-Parser: extrahiert eine Zahlenfolge aus einem
    Tag-String (z.B. 'v1.104.0' -> (1, 104, 0)). Faellt auf None zurueck,
    wenn kein Zahlenmuster gefunden wird - dann wird nur per String verglichen."""
    match = re.search(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?", tag or "")
    if not match:
        return None
    return tuple(int(g) if g else 0 for g in match.groups())


async def get_releases_between(github_repo: str, current_version: str, new_version: str) -> str:
    """Holt die Release-Notes aller GitHub-Releases zwischen current_version
    (exklusiv) und new_version (inklusiv) und gibt sie als zusammengefassten
    Text zurueck. Bei Parsing-Problemen wird konservativ mehr statt weniger
    zurueckgegeben (lieber zu viel Kontext fuer die Analyse als zu wenig)."""
    if not github_repo:
        return ""

    headers = {"Accept": "application/vnd.github+json"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"

    releases = []
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        for page in range(1, 4):  # max 300 Releases zurueck, sollte immer reichen
            resp = await client.get(
                f"https://api.github.com/repos/{github_repo}/releases",
                params={"per_page": 100, "page": page},
            )
            if resp.status_code == 404:
                return f"[Hinweis: GitHub-Repo '{github_repo}' nicht gefunden oder keine Releases.]"
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            releases.extend(batch)

    current_v = _parse_version(current_version)
    new_v = _parse_version(new_version)

    selected = []
    for rel in releases:
        tag = rel.get("tag_name", "")
        if rel.get("draft"):
            continue
        v = _parse_version(tag)

        if current_v is not None and new_v is not None and v is not None:
            if current_v < v <= new_v:
                selected.append(rel)
        else:
            # Kann nicht sauber parsen -> alles zwischen den beiden Tag-Namen
            # (String-Vorkommen) grob mitnehmen, damit nichts verloren geht.
            if tag in (current_version, new_version) or (
                current_version and current_version in tag
            ) or (new_version and new_version in tag):
                selected.append(rel)

    if not selected:
        # Fallback: nimm zumindest das Release des Ziel-Tags, falls vorhanden
        selected = [r for r in releases if r.get("tag_name") == new_version]

    parts = []
    for rel in sorted(selected, key=lambda r: r.get("published_at") or ""):
        parts.append(
            f"## {rel.get('tag_name')} ({rel.get('published_at', '')})\n"
            f"{rel.get('body') or '(keine Release-Notes)'}\n"
        )
    return "\n".join(parts) if parts else "[Keine passenden Releases zwischen den Versionen gefunden.]"
