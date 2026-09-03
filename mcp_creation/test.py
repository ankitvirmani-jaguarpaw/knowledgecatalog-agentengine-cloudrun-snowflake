import asyncio
import json
import google.auth.transport.requests
import google.oauth2.id_token
from fastmcp import Client

SERVICE_URL = "https://snowflake-catalog-283243243997.us-central1.run.app"
MCP_ENDPOINT = f"{SERVICE_URL}/sse"

async def test_snowflake():
    print(f"Generating ID token for {SERVICE_URL}...")
    auth_req = google.auth.transport.requests.Request()
    id_token = google.oauth2.id_token.fetch_id_token(auth_req, SERVICE_URL)

    print(f"Connecting to MCP SSE endpoint: {MCP_ENDPOINT}...")
    async with Client(MCP_ENDPOINT, auth=id_token) as client:
        print("\n--- 1. Listing Available Tools ---")
        tools = await client.list_tools()
        for t in tools:
            print(f"Tool detected: {t.name}")

        print("\n--- 2. Executing Test Query ---")
        query = (
            "SELECT count(c_customer_sk) AS total_customers "
            "FROM SNOWFLAKE_SAMPLE_DATA.TPCDS_SF100TCL.CUSTOMER"
        )
        response = await client.call_tool("run_sql_query", {"query": query})

        print("\nQuery Response:")
        raw = response.data if hasattr(response, "data") else response
        print(json.dumps(raw, indent=2))

if __name__ == "__main__":
    asyncio.run(test_snowflake())
