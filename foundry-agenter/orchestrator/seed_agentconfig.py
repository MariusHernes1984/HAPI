"""
Engangsskript: les eksisterende agenter fra Azure AI Foundry og fyll
agentconfig-tabellen med deres nåværende instructions+model.

Kjøres lokalt med az login eller i Container App med MI:
    cd foundry-agenter/orchestrator
    AGENT_CONFIG_STORAGE_ACCOUNT=<storage> python seed_agentconfig.py

For synthesis-agenten leses prompt+model fra orchestrate.py.
"""

import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed")


def _project_endpoint() -> str:
    return os.environ.get(
        "PROJECT_ENDPOINT",
        "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
    )


def _foundry_agents() -> list[dict]:
    """Returner [{name, id, instructions, model, description}] fra Foundry.

    SDK-en versjonerer agenter — vi henter `definition` fra nyeste/aktive
    versjon for å få instructions+model.
    """
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    endpoint = _project_endpoint()
    out = []
    with AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential()) as client:
        for a in client.agents.list():
            ad = a.as_dict() if hasattr(a, "as_dict") else dict(a)
            name = ad.get("name") or ""
            if not name:
                continue
            try:
                versions = list(client.agents.list_versions(name))
            except Exception as e:
                logger.warning(f"  list_versions({name}) feilet: {e}")
                versions = []
            if not versions:
                logger.info(f"  {name}: ingen versjoner — hopper over")
                continue
            vd = versions[0].as_dict()
            defn = vd.get("definition") or {}
            out.append({
                "name": name,
                "id": ad.get("id") or name,
                "instructions": defn.get("instructions") or "",
                "model": defn.get("model") or "",
                "description": ad.get("description") or vd.get("description") or "",
            })
    return out


def _synthesis_seed() -> dict:
    """Hent SYNTHESIS_PROMPT og SYNTH_MODEL fra orchestrate.py."""
    import orchestrate
    return {
        "name": "hapi-synthesis",
        "id": "",
        "instructions": orchestrate.SYNTHESIS_PROMPT,
        "model": orchestrate.SYNTH_MODEL,
        "description": "LLM-syntese av agent-svar (orchestrate.py)",
    }


def main():
    if not os.environ.get("AGENT_CONFIG_STORAGE_ACCOUNT") and not os.environ.get("CHATLOG_STORAGE_ACCOUNT"):
        logger.error("AGENT_CONFIG_STORAGE_ACCOUNT (eller CHATLOG_STORAGE_ACCOUNT) må være satt.")
        sys.exit(1)

    import agent_config
    store = agent_config.get_store(_project_endpoint())

    logger.info("Henter agenter fra Foundry…")
    try:
        agents = _foundry_agents()
    except Exception as e:
        logger.error(f"Foundry-listing feilet: {e}")
        agents = []

    logger.info(f"Fant {len(agents)} agent(er) i Foundry")
    agents.append(_synthesis_seed())

    seeded = 0
    for a in agents:
        if not a["name"]:
            continue
        # Behold eksisterende current-rad hvis admin allerede har redigert.
        existing = store.get_current(a["name"])
        if existing and existing.get("updated_by") not in ("", "seed"):
            logger.info(f"  beholder lokal versjon for {a['name']} (sist endret av {existing['updated_by']})")
            continue
        store.upsert_initial(
            agent_name=a["name"],
            prompt=a["instructions"],
            model=a["model"],
            foundry_agent_id=a["id"],
            description=a["description"],
        )
        logger.info(f"  seedet {a['name']} (model={a['model']}, prompt={len(a['instructions'])} tegn)")
        seeded += 1

    logger.info(f"Ferdig — {seeded} agent(er) seedet")


if __name__ == "__main__":
    main()
