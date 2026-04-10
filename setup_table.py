#!/usr/bin/env python3
"""Create the 'Competitor Ads' table in an Airtable base using pyairtable.

Reads schema.json and creates all fields. Skips if the table already exists.

Usage:
    python3 setup_table.py --base-id appXXXXXXXXXXXXXXX
"""
import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SCHEMA_PATH = Path(__file__).parent / "schema.json"
TABLE_NAME = "Competitor Ads"


def main():
    parser = argparse.ArgumentParser(description="Create Competitor Ads table in Airtable")
    parser.add_argument("--base-id", required=True, help="Airtable base ID (starts with app...)")
    args = parser.parse_args()

    pat = os.getenv("AIRTABLE_PAT")
    if not pat:
        sys.exit("AIRTABLE_PAT not set in .env — add it and try again.")

    from pyairtable import Api

    api = Api(pat)
    base = api.base(args.base_id)

    # Check if table already exists
    try:
        schema = base.schema()
        for t in schema.tables:
            if t.name == TABLE_NAME:
                print(f"Table '{TABLE_NAME}' already exists (id: {t.id}). Skipping creation.")
                print(f"Fields: {', '.join(f.name for f in t.fields)}")
                return
    except Exception as e:
        print(f"Warning: could not read base schema ({e}). Attempting table creation anyway.")

    # Load schema definition
    with open(SCHEMA_PATH) as f:
        schema_def = json.load(f)

    fields = schema_def["fields"]
    description = schema_def.get("description", "")

    print(f"Creating table '{TABLE_NAME}' with {len(fields)} fields...")

    try:
        table = base.create_table(
            TABLE_NAME,
            fields=fields,
            description=description,
        )
        print(f"Created! Table ID: {table.id}")
        print(f"Fields: {', '.join(f['name'] for f in fields)}")
    except Exception as e:
        sys.exit(f"Failed to create table: {e}")


if __name__ == "__main__":
    main()
