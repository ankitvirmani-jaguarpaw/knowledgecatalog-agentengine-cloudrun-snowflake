import asyncio
import logging
import os

import snowflake.connector
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)
logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)

mcp = FastMCP("Snowflake-Snowflake-MCP")


def get_private_key_der() -> bytes | None:
    """Parses RSA private key PEM from environment variable into DER bytes."""
    pem_str = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PEM")
    if not pem_str:
        return None

    passphrase = os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")
    passphrase_bytes = passphrase.encode() if passphrase else None

    # Handle single-line / escaped newlines from environment variables
    formatted_pem = pem_str.replace("\\n", "\n").strip().encode("utf-8")

    p_key = serialization.load_pem_private_key(
        formatted_pem,
        password=passphrase_bytes,
        backend=default_backend(),
    )

    return p_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@mcp.custom_route("/", methods=["GET"])
async def handle_root(request: Request) -> Response:
    """Handles GET requests to '/' for A2A health checks."""
    return Response(content="", media_type="text/event-stream")


@mcp.tool()
def run_sql_query(query: str) -> dict:
    """Use this to run read-only SQL queries on Snowflake.

    Args:
        query: The SQL query string to execute (e.g., "SELECT * FROM table LIMIT 10").

    Returns:
        A dictionary containing query results under 'data' or error message under 'error'.
    """
    logger.info(f"--- 🛠️ Tool: run_sql_query called with query: {query} ---")

    forbidden_keywords = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE"]
    if any(k in query.upper() for k in forbidden_keywords):
        logger.error(f"❌ Rejected non-read-only query: {query}")
        return {"error": "Read-only access only. Modification queries are blocked."}

    conn = None
    try:
        connect_kwargs = {
            "user": os.environ["SNOWFLAKE_USER"],
            "account": os.environ["SNOWFLAKE_ACCOUNT"],
            "role": os.environ.get("SNOWFLAKE_ROLE"),
            "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE"),
        }

        # Use Key-Pair authentication if private key is present, else fall back to password
        private_key_der = get_private_key_der()
        if private_key_der:
            connect_kwargs["private_key"] = private_key_der
        else:
            connect_kwargs["password"] = os.environ["SNOWFLAKE_PASSWORD"]

        conn = snowflake.connector.connect(**connect_kwargs)
        cursor = conn.cursor()
        cursor.execute(query)
        results = cursor.fetchall()

        logger.info(f"✅ Query successful. Rows returned: {len(results)}")
        return {"data": results}

    except snowflake.connector.errors.ProgrammingError as e:
        logger.error(f"❌ SQL execution failed: {e}")
        return {"error": f"SQL Error: {e}"}
    except Exception as e:
        logger.error(f"❌ Unexpected Error: {e}")
        return {"error": f"Unexpected error executing SQL: {e}"}
    finally:
        if conn and not conn.is_closed():
            conn.close()
            logger.info("🔒 Snowflake connection closed.")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 MCP server started on port {port} (SSE Transport)")

    asyncio.run(
        mcp.run_async(
            transport="sse",
            host="0.0.0.0",
            port=port,
        )
    )
