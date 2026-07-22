import json

from anthropic import AsyncAnthropic

from . import config

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


SYSTEM_PROMPT = """Du bist ein erfahrener Docker/DevOps-Ingenieur. Du bekommst \
die Release-Notes zwischen zwei Versionen eines Docker-Images sowie die aktuelle \
docker-compose.yml des betroffenen Stacks. Deine Aufgabe: dem Betreiber in klarem \
Deutsch sagen, ob das Update gefahrlos ist, was sich inhaltlich aendert, und ob die \
compose-Datei angepasst werden muss (z.B. neue Pflicht-Umgebungsvariablen, geaenderte \
Volumes/Ports, oder - besonders wichtig - fest gepinnte Versionen anderer Services in \
der compose-Datei, die zur neuen Version passen muessen, wie es z.B. bei immich \
regelmaessig vorkommt).

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, keine Erklaerung drumherum, exakt in \
diesem Format:
{
  "risk": "none" | "minor" | "major" | "breaking",
  "summary": "2-4 Saetze auf Deutsch, was sich aendert und worauf zu achten ist",
  "compose_change_needed": true | false,
  "compose_change_explanation": "falls compose_change_needed=true: was genau in der \
compose-Datei geaendert werden muss und warum. Sonst leerer String.",
  "compose_patch": "falls compose_change_needed=true UND du dir sicher bist, welche \
Zeile(n) sich aendern muessen: die VOLLSTAENDIGE, angepasste compose-Datei als String. \
Sonst leerer String - im Zweifel lieber nichts vorschlagen als etwas Falsches."
}
"""


async def analyze_update(
    container: str,
    image_name: str,
    current_version: str,
    new_version: str,
    release_notes: str,
    compose_content: str,
    extra_notes: str,
) -> dict:
    user_prompt = f"""Container: {container}
Image: {image_name}
Aktuelle Version: {current_version}
Neue Version: {new_version}

Zusaetzlicher Kontext vom Betreiber: {extra_notes or "(keiner)"}

--- Release-Notes zwischen den Versionen ---
{release_notes or "(keine gefunden)"}

--- Aktuelle docker-compose.yml des Stacks ---
{compose_content or "(nicht verfuegbar)"}
"""

    client = _get_client()
    response = await client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    text = "".join(block.text for block in response.content if block.type == "text")

    try:
        # Claude haelt sich fast immer ans JSON-Format, aber zur Sicherheit
        # den ersten { ... } Block extrahieren, falls doch Prosa drumherum steht.
        start = text.index("{")
        end = text.rindex("}") + 1
        data = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        return {
            "risk": "unknown",
            "summary": f"Antwort konnte nicht als JSON geparst werden. Rohtext: {text[:2000]}",
            "compose_change_needed": False,
            "compose_change_explanation": "",
            "compose_patch": "",
            "raw": text,
        }

    data.setdefault("risk", "unknown")
    data.setdefault("summary", "")
    data.setdefault("compose_change_needed", False)
    data.setdefault("compose_change_explanation", "")
    data.setdefault("compose_patch", "")
    data["raw"] = text
    return data
