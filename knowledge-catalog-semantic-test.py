import asyncio
import re
import google.auth
import google.auth.transport.requests
from fastmcp import Client

PROJECT_ID = "agents-demo-495412"
LOCATION = "us-central1"
ENTRY_GROUP_ID = "snowflake-tpcds"
KNOWLEDGE_CATALOG_MCP_URL = "https://dataplex.googleapis.com/mcp"

# Conceptual queries without using exact table names
TEST_QUERIES = [
    "where users live and their postal street addresses",
    "customer demographics like marital status and education",
    "returned merchandise and items brought back to stores",
]

async def run_semantic_test():
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/dataplex.readonly"]
    )
    auth_request = google.auth.transport.requests.Request()
    credentials.refresh(auth_request)

    async with Client(KNOWLEDGE_CATALOG_MCP_URL, auth=credentials.token) as client:
        for natural_prompt in TEST_QUERIES:
            print(f"\n==========================================")
            print(f"🔎 Testing Semantic Concept: '{natural_prompt}'")
            print(f"==========================================")

            # Natural language query scoped to the entry group
            scoped_query = f"entry_group:{ENTRY_GROUP_ID} {natural_prompt}"

            search_res = await client.call_tool(
                "search_entries",
                {
                    "projectId": PROJECT_ID,
                    "query": scoped_query,
                    "pageSize": 3,
                },
            )

            raw_text = str(search_res)

            # Match returned table entries
            pattern = rf"projects/[^/]+/locations/{LOCATION}/entryGroups/{ENTRY_GROUP_ID}/entries/[a-zA-Z0-9_\-]+"
            matches = list(dict.fromkeys(re.findall(pattern, raw_text)))
            tables = [m for m in matches if not m.endswith("_entry")]

            print(f"Discovered Tables ({len(tables)}):")
            for tbl in tables:
                print(" ->", tbl.split("/")[-1])

            if tables:
                context_res = await client.call_tool(
                    "lookup_context",
                    {
                        "projectId": PROJECT_ID,
                        "location": LOCATION,
                        "resources": [tables[0]],
                    },
                )
                if hasattr(context_res, "structured_content") and context_res.structured_content:
                    print("\nTop Match Context Summary:")
                    print(context_res.structured_content.get("context", "")[:400] + "...\n")

if __name__ == "__main__":
    asyncio.run(run_semantic_test())
