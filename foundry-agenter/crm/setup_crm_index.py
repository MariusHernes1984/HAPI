"""
Oppretter Azure AI Search-index for Kundealias CRM og synkroniserer data fra Dataverse.

Steg:
  1. Oppretter index 'kundealias-crm' i Azure AI Search
  2. Henter data fra Dataverse via Web API
  3. Laster data inn i indeksen

Forutsetninger:
  - Azure AI Search-tjeneste med admin-nøkkel
  - Dataverse-tilgang via az login (DefaultAzureCredential)
  - pip install azure-search-documents azure-identity requests

Bruk:
  python setup_crm_index.py
"""

import os
import json
import requests
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchIndex,
    SimpleField,
    SearchableField,
    SearchFieldDataType,
)

# --- Konfigurasjon ---

SEARCH_ENDPOINT = os.environ.get(
    "SEARCH_ENDPOINT", "https://kateaisearch.search.windows.net"
)
SEARCH_ADMIN_KEY = os.environ.get("SEARCH_ADMIN_KEY")  # Sett denne, eller bruk keyless
INDEX_NAME = "kundealias-crm"

# Dataverse
DATAVERSE_ENV_URL = os.environ.get(
    "DATAVERSE_URL", "https://orgf7788172.crm4.dynamics.com"
)
# Logisk navn på tabellen - sjekk i Power Apps / Dataverse
TABLE_LOGICAL_NAME = os.environ.get("DATAVERSE_TABLE", "cr4e8_kundealiascrms")


# --- Steg 1: Opprett index ---

def create_index():
    """Opprett Azure AI Search-index for Kundealias CRM."""
    if SEARCH_ADMIN_KEY:
        from azure.core.credentials import AzureKeyCredential
        index_client = SearchIndexClient(
            endpoint=SEARCH_ENDPOINT,
            credential=AzureKeyCredential(SEARCH_ADMIN_KEY),
        )
    else:
        index_client = SearchIndexClient(
            endpoint=SEARCH_ENDPOINT,
            credential=DefaultAzureCredential(),
        )

    fields = [
        SimpleField(
            name="id",
            type=SearchFieldDataType.String,
            key=True,
            filterable=True,
        ),
        SearchableField(
            name="customer_name",
            type=SearchFieldDataType.String,
            filterable=True,
            sortable=True,
        ),
        SearchableField(
            name="customer_alias",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="superoffice_id",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="acp_id",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        SimpleField(
            name="partnercenter_guid",
            type=SearchFieldDataType.String,
            filterable=True,
        ),
        # Sammenslatt tekstfelt for bedre soek
        SearchableField(
            name="search_text",
            type=SearchFieldDataType.String,
        ),
    ]

    index = SearchIndex(name=INDEX_NAME, fields=fields)

    try:
        index_client.delete_index(INDEX_NAME)
        print(f"  Slettet eksisterende index '{INDEX_NAME}'")
    except Exception:
        pass

    index_client.create_index(index)
    print(f"  Opprettet index '{INDEX_NAME}' med {len(fields)} felt")


# --- Steg 2: Hent data fra Dataverse ---

def fetch_dataverse_data() -> list[dict]:
    """Hent alle rader fra Kundealias CRM via Dataverse Web API."""
    credential = DefaultAzureCredential()
    token = credential.get_token("https://orgf7788172.crm4.dynamics.com/.default")

    headers = {
        "Authorization": f"Bearer {token.token}",
        "Accept": "application/json",
        "OData-MaxVersion": "4.0",
        "OData-Version": "4.0",
    }

    url = f"{DATAVERSE_ENV_URL}/api/data/v9.2/{TABLE_LOGICAL_NAME}"
    all_records = []

    while url:
        response = requests.get(url, headers=headers)
        if not response.ok:
            print(f"  FEIL fra Dataverse: {response.status_code} {response.text[:200]}")
            break

        data = response.json()
        records = data.get("value", [])
        all_records.extend(records)
        url = data.get("@odata.nextLink")

    print(f"  Hentet {len(all_records)} rader fra Dataverse")
    return all_records


# --- Steg 3: Indekser data ---

def index_data(records: list[dict]):
    """Last data inn i Azure AI Search-indeksen."""
    if SEARCH_ADMIN_KEY:
        from azure.core.credentials import AzureKeyCredential
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=INDEX_NAME,
            credential=AzureKeyCredential(SEARCH_ADMIN_KEY),
        )
    else:
        search_client = SearchClient(
            endpoint=SEARCH_ENDPOINT,
            index_name=INDEX_NAME,
            credential=DefaultAzureCredential(),
        )

    # Map Dataverse-felt til index-felt
    # NB: Feltnavnene fra Dataverse kan variere - tilpass ved behov
    documents = []
    for i, rec in enumerate(records):
        # Proev vanlige Dataverse-feltnavn
        name = (
            rec.get("cr4e8_customername")
            or rec.get("cr4e8_name")
            or rec.get("name")
            or rec.get("cr4e8_kundealiascrm")
            or ""
        )
        alias = rec.get("cr4e8_customeralias", "")
        superoffice = rec.get("cr4e8_superoffice_id", rec.get("cr4e8_supoerofficeid", ""))
        acp = rec.get("cr4e8_acp_id", rec.get("cr4e8_acpid", ""))
        partner = rec.get("cr4e8_partnercenter_guid", rec.get("cr4e8_partnercenterguid", ""))

        # Fallback: bruk foerste string-verdier
        if not name:
            for k, v in rec.items():
                if isinstance(v, str) and not k.startswith("_") and not k.startswith("@"):
                    name = v
                    break

        doc = {
            "id": rec.get(f"{TABLE_LOGICAL_NAME[:-1]}id", str(i)),
            "customer_name": str(name),
            "customer_alias": str(alias),
            "superoffice_id": str(superoffice),
            "acp_id": str(acp),
            "partnercenter_guid": str(partner),
            "search_text": f"{name} {alias} SuperOffice:{superoffice} ACP:{acp}",
        }
        documents.append(doc)

    if documents:
        batch_size = 1000
        for i in range(0, len(documents), batch_size):
            batch = documents[i : i + batch_size]
            result = search_client.upload_documents(batch)
            succeeded = sum(1 for r in result if r.succeeded)
            print(f"  Indeksert batch {i // batch_size + 1}: {succeeded}/{len(batch)} OK")
    else:
        print("  Ingen dokumenter aa indeksere")


# --- Alternativ: Last fra JSON-fil ---

def index_from_json(filepath: str):
    """Last data fra en JSON-eksport i stedet for Dataverse API."""
    with open(filepath, "r", encoding="utf-8") as f:
        records = json.load(f)
    print(f"  Lastet {len(records)} rader fra {filepath}")
    index_data(records)


def main():
    print("=== Kundealias CRM -> Azure AI Search ===\n")

    print("Steg 1: Oppretter index...")
    create_index()

    print("\nSteg 2: Henter data fra Dataverse...")
    try:
        records = fetch_dataverse_data()
        print("\nSteg 3: Indekserer data...")
        index_data(records)
    except Exception as e:
        print(f"  Kunne ikke hente fra Dataverse: {e}")
        print("  Tips: Eksporter tabellen som JSON og kjoer:")
        print("    python setup_crm_index.py --json eksport.json")
        return

    print("\n=== Ferdig! ===")
    print(f"Index '{INDEX_NAME}' er klar paa {SEARCH_ENDPOINT}")
    print("Neste steg: Kjoer deploy_agents.py for aa opprette CRM-agenten")


if __name__ == "__main__":
    import sys
    if "--json" in sys.argv:
        idx = sys.argv.index("--json")
        json_file = sys.argv[idx + 1]
        print("=== Kundealias CRM -> Azure AI Search (fra JSON) ===\n")
        print("Steg 1: Oppretter index...")
        create_index()
        print("\nSteg 2+3: Indekserer fra JSON...")
        index_from_json(json_file)
        print(f"\n=== Ferdig! Index '{INDEX_NAME}' er klar ===")
    else:
        main()
