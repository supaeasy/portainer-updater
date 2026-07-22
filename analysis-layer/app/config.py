import os
import yaml

STACKS_FILE = os.environ.get("STACKS_FILE", "/app/stacks.yml")

WUD_API_URL = os.environ.get("WUD_API_URL", "http://wud:3000")
PORTAINER_URL = os.environ.get("PORTAINER_URL", "").rstrip("/")
PORTAINER_API_KEY = os.environ.get("PORTAINER_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ANALYSIS_POLL_INTERVAL_MINUTES = int(
    os.environ.get("ANALYSIS_POLL_INTERVAL_MINUTES", "60")
)
DB_PATH = os.environ.get("DB_PATH", "/app/data/updates.db")


def load_stacks_config():
    """Liest stacks.yml und liefert ein dict: container_name -> config."""
    if not os.path.exists(STACKS_FILE):
        return {}

    with open(STACKS_FILE, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    result = {}
    for entry in raw.get("stacks", []):
        container = entry.get("container")
        if not container:
            continue
        result[container] = {
            "container": container,
            "portainer_stack_name": entry.get("portainer_stack_name", container),
            "portainer_endpoint_id": entry.get("portainer_endpoint_id"),
            "github_repo": entry.get("github_repo"),
            "notes": entry.get("notes", "") or "",
        }
    return result
