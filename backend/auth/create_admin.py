"""Create an administrator account from the command line (first-time setup or recovery).

    python -m backend.auth.create_admin

Further users are created by an administrator on the User management page.
The password is typed with hidden input and only its scrypt hash is stored.
"""

from __future__ import annotations

import getpass
import sys

from backend.auth import service
from backend.database import init_db


def main() -> int:
    init_db.apply_schema()
    print("Create a dashboard administrator.")
    email = input("Email address (used to sign in): ").strip()
    full_name = input("Full name: ").strip()
    password = getpass.getpass("Password (hidden): ")
    if password != getpass.getpass("Repeat password: "):
        print("The passwords do not match. Nothing was created.", file=sys.stderr)
        return 1
    try:
        user = service.create_user(None, {"email": email, "fullName": full_name,
                                          "role": "admin", "password": password})
    except service.AuthError as error:
        print(f"Not created: {error}", file=sys.stderr)
        return 1
    print(f"Administrator {user['email']} created. Sign in at the dashboard with this email to add other users.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
