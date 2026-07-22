import httpx

from . import config


def _headers():
    return {"X-API-Key": config.PORTAINER_API_KEY}


async def list_stacks() -> list[dict]:
    async with httpx.AsyncClient(timeout=30, headers=_headers()) as client:
        resp = await client.get(f"{config.PORTAINER_URL}/api/stacks")
        resp.raise_for_status()
        return resp.json()


async def get_stack(stack_id: int) -> dict:
    async with httpx.AsyncClient(timeout=30, headers=_headers()) as client:
        resp = await client.get(f"{config.PORTAINER_URL}/api/stacks/{stack_id}")
        resp.raise_for_status()
        return resp.json()


async def get_stack_file(stack_id: int) -> str:
    async with httpx.AsyncClient(timeout=30, headers=_headers()) as client:
        resp = await client.get(f"{config.PORTAINER_URL}/api/stacks/{stack_id}/file")
        resp.raise_for_status()
        return resp.json().get("StackFileContent", "")


async def find_stack_by_name(name: str) -> dict | None:
    stacks = await list_stacks()
    for s in stacks:
        if s.get("Name") == name:
            return s
    return None


async def update_stack(
    stack_id: int,
    endpoint_id: int,
    stack_file_content: str,
    env: list | None = None,
    repull_and_redeploy: bool = True,
) -> dict:
    """Schreibt eine (ggf. angepasste) compose-Datei zurueck und redeployed
    den Stack. Bestehende Env-Variablen werden unveraendert durchgereicht,
    damit sie beim Update nicht verloren gehen."""
    payload = {
        "StackFileContent": stack_file_content,
        "Env": env or [],
        "Prune": False,
        "RepullImageAndRedeploy": repull_and_redeploy,
    }
    async with httpx.AsyncClient(timeout=120, headers=_headers()) as client:
        resp = await client.put(
            f"{config.PORTAINER_URL}/api/stacks/{stack_id}",
            params={"endpointId": endpoint_id},
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()
