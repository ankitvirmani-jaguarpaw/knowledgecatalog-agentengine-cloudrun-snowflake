import snowflake.connector
from google.cloud import dataplex_v1
from google.protobuf import struct_pb2
from google.api_core import exceptions  

# ─── 1. CONFIGURATION ───
GCP_PROJECT_ID = "agents-demo-495412"
GCP_LOCATION = "us-central1"
ENTRY_GROUP_ID = "snowflake-tpcds"

# Custom Entry Type in your project/location
CUSTOM_ENTRY_TYPE = f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/entryTypes/snowflake-table"

# System aspect types
SYSTEM_SCHEMA_ASPECT_TYPE = "projects/dataplex-types/locations/global/aspectTypes/schema"
SYSTEM_OVERVIEW_ASPECT_TYPE = "projects/dataplex-types/locations/global/aspectTypes/overview"

###Please fill these values#####
SNOWFLAKE_CONFIG = {
    "user": "",
    "password": "",
    "account": "",
    "warehouse": "COMPUTE_WH",
    "database": "SNOWFLAKE_SAMPLE_DATA",
    "schema": "TPCDS_SF100TCL"
}

# ─── 2. HELPER: MAP SNOWFLAKE TYPES TO DATAPLEX ENUM ───
def map_snowflake_to_dataplex_type(sf_type: str) -> str:
    sf_type = sf_type.upper()
    if any(t in sf_type for t in ["NUMBER", "INT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC"]):
        return "NUMBER"
    if any(t in sf_type for t in ["VARCHAR", "CHAR", "TEXT", "STRING"]):
        return "STRING"
    if "BOOLEAN" in sf_type:
        return "BOOLEAN"
    if "TIMESTAMP" in sf_type:
        return "TIMESTAMP"
    if any(t in sf_type for t in ["DATE", "TIME"]):
        return "DATETIME"
    if any(t in sf_type for t in ["BINARY", "VARBINARY"]):
        return "BYTES"
    if any(t in sf_type for t in ["OBJECT", "VARIANT", "ARRAY"]):
        return "STRUCT"
    if any(t in sf_type for t in ["GEOGRAPHY", "GEOMETRY"]):
        return "GEOSPATIAL"
    return "OTHER"

# ─── 3. INITIALIZE CLIENTS ───
catalog_client = dataplex_v1.CatalogServiceClient()
sf_conn = snowflake.connector.connect(**SNOWFLAKE_CONFIG)
sf_cursor = sf_conn.cursor()

parent_entry_group = (
    f"projects/{GCP_PROJECT_ID}/locations/{GCP_LOCATION}/entryGroups/{ENTRY_GROUP_ID}"
)

schema_name = SNOWFLAKE_CONFIG["schema"]
db_name = SNOWFLAKE_CONFIG["database"]

# ─── 4. FETCH TABLE METADATA FROM SNOWFLAKE ───
print(f"🔍 Querying tables for schema: {schema_name}...")
sf_cursor.execute(f"""
    SELECT TABLE_NAME, TABLE_TYPE, COMMENT
    FROM {db_name}.INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = '{schema_name}'
""")
tables = sf_cursor.fetchall()
print(f"📋 Found {len(tables)} tables to process.")

# ─── 5. SYNC EACH TABLE INTO KNOWLEDGE CATALOG ───
for table_name, table_type, table_comment in tables:
    print(f"\n⚙️ Processing table: {table_name}...")

    sf_cursor.execute(f"""
        SELECT 
            COLUMN_NAME, 
            DATA_TYPE, 
            IS_NULLABLE, 
            COMMENT
        FROM {db_name}.INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = '{schema_name}' AND TABLE_NAME = '{table_name}'
        ORDER BY ORDINAL_POSITION
    """)
    columns = sf_cursor.fetchall()

    fields_list = []
    for col_name, data_type, is_nullable, col_comment in columns:
        fields_list.append({
            "name": col_name,
            "dataType": data_type,
            "metadataType": map_snowflake_to_dataplex_type(data_type),
            "mode": "NULLABLE" if is_nullable == "YES" else "REQUIRED",
            "description": col_comment or ""
        })

    entry_id = f"{schema_name.lower()}_{table_name.lower()}".replace("-", "_")

    # Build Schema Aspect
    schema_aspect_struct = struct_pb2.Struct()
    schema_aspect_struct.update({"fields": fields_list})

    # Build Overview Aspect
    overview_aspect_struct = struct_pb2.Struct()
    overview_aspect_struct.update({
        "content": table_comment or f"Snowflake table {schema_name}.{table_name}"
    })

    # Instantiate Dataplex Entry
    entry = dataplex_v1.Entry()
    entry.entry_type = CUSTOM_ENTRY_TYPE
    entry.aspects = {
        "dataplex-types.global.schema": dataplex_v1.Aspect(
            aspect_type=SYSTEM_SCHEMA_ASPECT_TYPE,
            data=schema_aspect_struct
        ),
        "dataplex-types.global.overview": dataplex_v1.Aspect(
            aspect_type=SYSTEM_OVERVIEW_ASPECT_TYPE,
            data=overview_aspect_struct
        )
    }

    entry_full_name = f"{parent_entry_group}/entries/{entry_id}"

    # Upsert Entry
    try:
        try:
            catalog_client.get_entry(name=entry_full_name)
            entry.name = entry_full_name
            catalog_client.update_entry(entry=entry)
            print(f"🔄 Updated entry: {entry_id}")
        except exceptions.NotFound:
            catalog_client.create_entry(
                parent=parent_entry_group,
                entry_id=entry_id,
                entry=entry
            )
            print(f"✅ Created entry: {entry_id}")
    except Exception as e:
        print(f"❌ Failed to sync {table_name}: {e}")

sf_cursor.close()
sf_conn.close()
print("\n🎉 Schema sync operation complete.")
