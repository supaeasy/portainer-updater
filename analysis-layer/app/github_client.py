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


def _headers():
    headers = {"Accept": "application/vnd.github+json"}
    if config.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return headers


async def _fetch_all_releases(github_repo: str) -> list[dict]:
    """Holt alle (nicht-draft) Releases eines Repos, neueste zuerst limitiert
    auf max. 300 (3 Seiten a 100) - reicht in der Praxis immer."""
    releases = []
    async with httpx.AsyncClient(timeout=30, headers=_headers()) as client:
        for page in range(1, 4):
            resp = await client.get(
                f"https://api.github.com/repos/{github_repo}/releases",
                params={"per_page": 100, "page": page},
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            releases.extend(batch)
    return [r for r in releases if not r.get("draft")]


async def get_releases_between(github_repo: str, current_version: str, new_version: str) -> str:
    """Holt die Release-Notes aller GitHub-Releases zwischen current_version
    (exklusiv) und new_version (inklusiv) und gibt sie als zusammengefassten
    Text zurueck. Bei Parsing-Problemen wird konservativ mehr statt weniger
    zurueckgegeben (lieber zu viel Kontext fuer die Analyse als zu wenig)."""
    if not github_repo:
        return ""

    releases = await _fetch_all_releases(github_repo)
    if not releases:
        return f"[Hinweis: GitHub-Repo '{github_repo}' nicht gefunden oder keine Releases.]"

    current_v = _parse_version(current_version)
    new_v = _parse_version(new_version)

    selected = []
    for rel in releases:
        tag = rel.get("tag_name", "")
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


async def get_latest_release(github_repo: str) -> dict | None:
    """Neuestes Release eines Repos (bevorzugt keine Pre-Releases, falls es
    auch reguläre Releases gibt). Fuer Images ohne Versions-Tag (:latest) die
    Grundlage, um zu wissen, worauf ueberhaupt aktualisiert werden koennte."""
    releases = await _fetch_all_releases(github_repo)
    if not releases:
        return None
    stable = [r for r in releases if not r.get("prerelease")]
    candidates = stable or releases
    return max(candidates, key=lambda r: r.get("published_at") or "")


async def get_release_at_or_before(github_repo: str, iso_date: str) -> dict | None:
    """Naechstliegendes Release am oder vor einem gegebenen Zeitpunkt.

    Heuristik fuer Container ohne Versions-Tag (z.B. :latest): wir kennen zwar
    nicht die exakte laufende Version, aber das Erstellungsdatum des lokal
    gezogenen Images - das naechste Release davor ist eine brauchbare
    Naeherung fuer 'was momentan vermutlich laeuft'."""
    if not iso_date:
        return None
    releases = await _fetch_all_releases(github_repo)
    candidates = [r for r in releases if (r.get("published_at") or "") <= iso_date]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.get("published_at") or "")
