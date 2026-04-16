"""
Opprett Azure AI Foundry vector store fra eksporterte retningslinje-filer.

Flyt:
  1. Last opp alle Markdown-filer til Azure AI Foundry (OpenAI files API)
  2. Opprett en vector store
  3. Legg alle filer inn i vector store via file_batches
  4. Vent på at indeksering er fullført
  5. Lagre vector_store_id for bruk i deploy_agents.py

Bruk:
  python create_vectorstore.py [--input-dir ./retningslinjer_export] [--name "HAPI Retningslinjer"]
"""

import argparse
import json
import os
import time
import logging
from pathlib import Path

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

logger = logging.getLogger(__name__)

PROJECT_ENDPOINT = os.environ.get(
    "PROJECT_ENDPOINT",
    "https://kateecosystem-resource.services.ai.azure.com/api/projects/kateecosystem",
)


def upload_files(openai_client, input_dir: Path) -> list[str]:
    """Last opp alle Markdown-filer til Azure AI Foundry."""
    md_files = sorted(input_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"Ingen .md-filer funnet i {input_dir}")

    logger.info(f"Laster opp {len(md_files)} filer...")
    file_ids = []

    for i, md_file in enumerate(md_files, 1):
        try:
            with open(md_file, "rb") as f:
                uploaded = openai_client.files.create(
                    file=f,
                    purpose="assistants",
                )
            file_ids.append(uploaded.id)
            if i % 10 == 0 or i == len(md_files):
                logger.info(f"  {i}/{len(md_files)} filer lastet opp")
        except Exception as e:
            logger.error(f"  FEIL ved opplasting av {md_file.name}: {e}")

    logger.info(f"  Totalt {len(file_ids)} filer lastet opp")
    return file_ids


def create_vector_store(openai_client, name: str, file_ids: list[str]) -> str:
    """Opprett vector store og legg til filer."""
    logger.info(f"Oppretter vector store '{name}'...")

    # Opprett vector store
    vs = openai_client.vector_stores.create(name=name)
    vs_id = vs.id
    logger.info(f"  Vector store opprettet: {vs_id}")

    # Legg til filer i batches (max 500 per batch)
    batch_size = 100
    for i in range(0, len(file_ids), batch_size):
        batch = file_ids[i:i + batch_size]
        logger.info(f"  Legger til batch {i // batch_size + 1} ({len(batch)} filer)...")

        batch_result = openai_client.vector_stores.file_batches.create(
            vector_store_id=vs_id,
            file_ids=batch,
        )
        logger.info(f"    Batch ID: {batch_result.id}, status: {batch_result.status}")

    return vs_id


def wait_for_ready(openai_client, vs_id: str, timeout_s: int = 600) -> bool:
    """Vent på at vector store er ferdig indeksert."""
    logger.info(f"Venter på indeksering (maks {timeout_s}s)...")
    start = time.monotonic()

    while True:
        vs = openai_client.vector_stores.retrieve(vs_id)
        status = vs.status
        counts = vs.file_counts

        elapsed = int(time.monotonic() - start)
        logger.info(
            f"  [{elapsed}s] Status: {status} | "
            f"completed: {counts.completed}, in_progress: {counts.in_progress}, "
            f"failed: {counts.failed}, total: {counts.total}"
        )

        if status == "completed":
            logger.info(f"  Vector store er klar!")
            return True

        if elapsed > timeout_s:
            logger.error(f"  TIMEOUT etter {timeout_s}s")
            return False

        if counts.in_progress == 0 and counts.failed > 0:
            logger.error(f"  {counts.failed} filer feilet og ingen er i progress")
            return False

        time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description="Opprett vector store fra retningslinje-filer")
    parser.add_argument("--input-dir", default="./retningslinjer_export", help="Mappe med Markdown-filer")
    parser.add_argument("--name", default="HAPI Retningslinjer", help="Navn på vector store")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )

    input_dir = Path(args.input_dir)
    if not input_dir.exists():
        logger.error(f"Input-mappe finnes ikke: {input_dir}")
        return

    logger.info(f"Kobler til Azure AI Foundry: {PROJECT_ENDPOINT}")

    client = AIProjectClient(
        endpoint=PROJECT_ENDPOINT,
        credential=DefaultAzureCredential(),
    )
    openai = client.get_openai_client()

    # Steg 1: Last opp filer
    file_ids = upload_files(openai, input_dir)
    if not file_ids:
        logger.error("Ingen filer lastet opp!")
        return

    # Steg 2: Opprett vector store
    vs_id = create_vector_store(openai, args.name, file_ids)

    # Steg 3: Vent på indeksering
    ready = wait_for_ready(openai, vs_id)

    # Steg 4: Lagre resultater
    vs = openai.vector_stores.retrieve(vs_id)
    result = {
        "vector_store_id": vs_id,
        "name": args.name,
        "status": vs.status,
        "file_counts": {
            "total": vs.file_counts.total,
            "completed": vs.file_counts.completed,
            "failed": vs.file_counts.failed,
            "in_progress": vs.file_counts.in_progress,
        },
        "file_ids": file_ids,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    output_file = Path(__file__).parent / "vectorstore_config.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    logger.info(f"\n{'='*60}")
    logger.info(f"VECTOR STORE {'KLAR' if ready else 'IKKE KLAR'}")
    logger.info(f"  ID: {vs_id}")
    logger.info(f"  Filer: {vs.file_counts.completed}/{vs.file_counts.total}")
    logger.info(f"  Konfig lagret til: {output_file}")
    logger.info(f"\nBruk i deploy_agents.py:")
    logger.info(f'  FileSearchTool(vector_store_ids=["{vs_id}"])')


if __name__ == "__main__":
    main()
