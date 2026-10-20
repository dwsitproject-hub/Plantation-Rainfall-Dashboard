"""One-time setup: create the application role and database, then the schema.

Run once on the PostgreSQL 18 server by a database administrator:

    python -m backend.database.bootstrap

It asks for a superuser login (input hidden) and uses DATABASE_USER,
DATABASE_PASSWORD and DATABASE_NAME from .env for the application account.
The superuser password is never stored.
"""

from __future__ import annotations

import getpass
import sys

from psycopg2 import sql

from backend import config
from backend.database import init_db
from backend.database.db import connect


def main() -> int:
    admin_user = input("PostgreSQL superuser [postgres]: ").strip() or "postgres"
    admin_password = getpass.getpass(f"Password for {admin_user} (input hidden): ")
    app_user, app_password = config.database_user(), config.database_password()

    admin = connect(user=admin_user, password=admin_password, dbname="postgres")
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute("SHOW server_version_num")
            version = int(cursor.fetchone()[0])
            if version < 180000:
                print(f"Warning: server version {version} is older than PostgreSQL 18.", file=sys.stderr)
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_user,))
            verb = "ALTER" if cursor.fetchone() else "CREATE"
            cursor.execute(sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(app_user), sql.Literal(app_password)))
            print(f"{verb.title()}d role {app_user}.")
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (config.DATABASE_NAME,))
            if cursor.fetchone():
                print(f"Database {config.DATABASE_NAME} already exists.")
            else:
                cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(
                    sql.Identifier(config.DATABASE_NAME), sql.Identifier(app_user)))
                print(f"Created database {config.DATABASE_NAME}.")
    finally:
        admin.close()

    database = connect(user=admin_user, password=admin_password)
    database.autocommit = True
    try:
        with database.cursor() as cursor:
            cursor.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                sql.Identifier(config.DATABASE_NAME)))
            cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS rainfall AUTHORIZATION {}").format(
                sql.Identifier(app_user)))
    finally:
        database.close()

    return init_db.main()


if __name__ == "__main__":
    raise SystemExit(main())
