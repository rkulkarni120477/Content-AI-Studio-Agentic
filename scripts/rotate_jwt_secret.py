"""
Rotate the JWT secret key.

When the secret changes, all existing tokens are immediately invalidated
and all active users must log in again.

Run this:
  - When you suspect the secret has been leaked
  - After cloning from the repository to a new server
  - As part of a regular security rotation schedule

Usage
-----
    python scripts/rotate_jwt_secret.py

The script prints a new secret.  Paste it into your .env file as
JWT_SECRET_KEY and restart the API server.
"""

import secrets


def main() -> None:
    new_secret = secrets.token_hex(32)   # 256-bit random secret

    print("New JWT secret key (paste into .env):")
    print()
    print(f"JWT_SECRET_KEY={new_secret}")
    print()
    print("WARNING: Rotating this key will log out ALL active users.")
    print("Restart the API server after updating .env.")


if __name__ == "__main__":
    main()
