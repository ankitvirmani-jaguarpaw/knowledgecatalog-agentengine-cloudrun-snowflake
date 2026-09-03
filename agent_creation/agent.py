from __future__ import annotations

import json
import logging
import os
import re
import time
import google.auth
import google.auth.transport.requests
import google.oauth2.id_token
from fastmcp import Client
from google.cloud import storage

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.apps import App
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# =====================================================================
# Configuration & Endpoints
# =====================================================================
PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "agents-demo-495412")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
ENTRY_GROUP_ID = "snowflake-tpcds"

# Official Knowledge Catalog Remote MCP Discovery Endpoint
KNOWLEDGE_CATALOG_MCP_URL = "https://dataplex.googleapis.com/mcp"

# Snowflake MCP Cloud Run Endpoint
SNOWFLAKE_MCP_URL = os.getenv(
    "MCP_URL",
    "https://snowflake-catalog-283243243997.us-central1.run.app/sse",
)
GCS_BUCKET_NAME = "sf-agent"
agent_engine_id = os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID")


# =====================================================================
# Tool 1: Knowledge Catalog Remote MCP (Discovery & Schema Context)
# =====================================================================
async def search_knowledge_catalog(search_query: str) -> str:
    """Searches GCP Knowledge Catalog via remote MCP to semantically discover
    table schemas, column names, definitions, data types, and aspect metadata.

    Args:
        search_query: Plain conceptual description, business keyword, or table name
                      (e.g., 'customer demographics like marital status', 'items returned', 'store sales').
    """
    try:
        credentials, _ = google.auth.default(
        scopes=[
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/dataplex.readonly",
    ])
        auth_request = google.auth.transport.requests.Request()
        credentials.refresh(auth_request)

        # Ensure query targets the entry group without duplicate prefixes
        clean_term = search_query.replace(f"entry_group:{ENTRY_GROUP_ID}", "").strip()
        formatted_query = f"entry_group:{ENTRY_GROUP_ID} {clean_term}".strip()

        async with Client(KNOWLEDGE_CATALOG_MCP_URL, auth=credentials.token) as kc_client:
            # Step 1: Semantic search using validated arguments
            search_response = await kc_client.call_tool(
                "search_entries",
                {
                    "projectId": PROJECT_ID,
                    "query": formatted_query,
                    "pageSize": 5,
                },
            )

            raw_text = str(search_response)

            # Step 2: Extract resource entries (matching project number or project ID)
            entry_pattern = rf"projects/[^/]+/locations/{LOCATION}/entryGroups/{ENTRY_GROUP_ID}/entries/[a-zA-Z0-9_\-]+"
            all_entries = list(dict.fromkeys(re.findall(entry_pattern, raw_text)))

            # Exclude entryGroup root entries
            table_entries = [e for e in all_entries if not e.endswith("_entry")]

            if not table_entries:
                return f"No tables found in Knowledge Catalog matching query: '{search_query}'"

            # Step 3: Fetch LLM-ready YAML schema metadata
            context_response = await kc_client.call_tool(
                "lookup_context",
                {
                    "projectId": PROJECT_ID,
                    "location": LOCATION,
                    "resources": table_entries[:3],
                },
            )

            if hasattr(context_response, "structured_content") and context_response.structured_content:
                return context_response.structured_content.get("context", str(context_response))
            elif hasattr(context_response, "content") and context_response.content:
                return context_response.content[0].text

            return str(context_response)

    except Exception as e:
        logger.error(f"Error in search_knowledge_catalog: {e}")
        return f"Knowledge Catalog MCP Error: {str(e)}"


# =====================================================================
# Tool 2: Snowflake Remote MCP Execution
# =====================================================================
async def run_snowflake_sql(sql_query: str) -> str:
    """Runs a read-only SQL query on Snowflake via the remote Snowflake MCP server.
    Use ONLY when actual data values or aggregated metrics are required.

    Args:
        sql_query: Fully qualified SQL statement (e.g., SNOWFLAKE_SAMPLE_DATA.TPCDS_SF100TCL.CUSTOMER).
    """
    try:
        mcp_audience = SNOWFLAKE_MCP_URL.replace("/sse", "")
        token = google.oauth2.id_token.fetch_id_token(
            google.auth.transport.requests.Request(), mcp_audience
        )

        async with Client(SNOWFLAKE_MCP_URL, auth=token) as mcp_client:
            resp = await mcp_client.call_tool("run_sql_query", {"query": sql_query})
            raw_data = (
                resp.data
                if hasattr(resp, "data")
                else (resp.get("data") if isinstance(resp, dict) else resp)
            )
            return json.dumps({"sql_used": sql_query, "result": raw_data})
    except Exception as e:
        logger.error(f"Error executing Snowflake SQL: {e}")
        return f"Snowflake Tool Error: {str(e)}"


# =====================================================================
# Tool 3 & 4: Dashboard Publisher & Google Search
# =====================================================================
def upload_dashboard_to_gcs(html_content: str) -> str:
    """Uploads the generated Tailwind HTML dashboard to Google Cloud Storage and returns the public URL."""
    try:
        storage_client = storage.Client()
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        file_name = f"snowflake_report_{int(time.time())}.html"

        blob = bucket.blob(file_name)
        blob.upload_from_string(html_content, content_type="text/html")
        return f"https://storage.googleapis.com/{GCS_BUCKET_NAME}/{file_name}"
    except Exception as e:
        logger.error(f"Error uploading report to GCS: {e}")
        return f"Upload Error: {str(e)}"


def search_web_for_state_trends(state_query: str) -> str:
    """Queries Google Search for live regional macro-economic or consumer trends."""
    try:
        from google import genai
        from google.genai import types

        client = genai.Client()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"Provide a brief analytical summary of recent economic or business trends for: {state_query}",
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            ),
        )
        return response.text
    except Exception as e:
        return f"Web Search Error: {str(e)}"


# =====================================================================
# Memory Management
# =====================================================================
async def _save_memory(callback_context: CallbackContext) -> None:
    """Persists conversational memory to Agent Engine Memory Bank."""
    if agent_engine_id:
        try:
            await callback_context.add_session_to_memory()
        except Exception as e:
            logger.warning(f"Memory persistence bypassed: {e}")


# =====================================================================
# Agent Prompt Instructions & App Assembly
# =====================================================================
COMBINED_INSTRUCTION = """
You are an expert business intelligence coordinator and data analyst agent.

Strict Separation of Concerns:
1. METADATA & SCHEMA DISCOVERY (Knowledge Catalog MCP):
   - Whenever you need to know which tables exist, what columns are available, data types, relationships, or documentation, invoke `search_knowledge_catalog`.
   - Pass descriptive concepts or table topics (e.g., 'customer demographics', 'household income', 'sales returns') to take advantage of semantic discovery.
   - NEVER query Snowflake to inspect metadata, column lists, or table definitions (`DESCRIBE TABLE`, `INFORMATION_SCHEMA`, etc.).

2. DATA RETRIEVAL (Snowflake MCP):
   - Invoke `run_snowflake_sql` ONLY when the user explicitly requests actual business data, aggregates, metrics, or row values.
   - Construct valid, read-only SQL referencing fully qualified tables (e.g., `SNOWFLAKE_SAMPLE_DATA.TPCDS_SF100TCL.<TABLE_NAME>`) derived directly from the Knowledge Catalog schema context.
   - Keep queries bounded with LIMIT clauses and selective aggregation.

3. OPTIONAL MACRO INSIGHTS & DASHBOARD:
   - When the user asks for broader regional analysis or reports, call `search_web_for_state_trends` on relevant regions from the results.
   - When requested, compile the data and insights into a Tailwind CSS dashboard and publish it via `upload_dashboard_to_gcs`.

Response Guidelines:
- If the user only asked about schema or data structures, answer completely using the Knowledge Catalog context without touching Snowflake.
- If data was requested, summarize the business insights, provide the SQL query used in a code block, and present key metrics in a clean Markdown table.
- If a dashboard was generated, include the public GCS URL. Never dump raw HTML directly in the conversational response.
"""

root_agent = LlmAgent(
    name="snowflake_catalog_agent",
    model="gemini-2.5-pro",
    instruction=COMBINED_INSTRUCTION,
    tools=[
        search_knowledge_catalog,
        run_snowflake_sql,
        upload_dashboard_to_gcs,
        search_web_for_state_trends,
        PreloadMemoryTool(),
    ],
    after_agent_callback=_save_memory,
)

app = App(
    name="snowflake_catalog_agent",
    root_agent=root_agent,
)
