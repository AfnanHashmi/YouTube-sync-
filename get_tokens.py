#!/usr/bin/env python3
"""One-time helper: sign in to both YouTube accounts and print the GitHub secrets.

Run this on your own computer (it opens a browser), with client_secret.json
downloaded from Google Cloud in the same folder. Nothing is saved to disk.
"""

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube"]
CLIENT_SECRET_FILE = "client_secret.json"


def sign_in(label):
    print(f"\n>>> A browser window will open. Sign in with your {label} account.")
    input("    Press Enter when ready...")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    # prompt=consent makes Google always return a refresh token.
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    items = youtube.channels().list(part="snippet", mine=True).execute().get("items") or []
    name = items[0]["snippet"]["title"] if items else "(no YouTube channel found!)"
    print(f"    Signed in as channel: {name}")
    return creds.refresh_token, name


def main():
    try:
        with open(CLIENT_SECRET_FILE, encoding="utf-8") as f:
            info = json.load(f)
    except FileNotFoundError:
        sys.exit(f"{CLIENT_SECRET_FILE} not found. Download it from Google Cloud (see README.md).")
    client = info.get("installed") or info.get("web") or {}

    main_token, main_name = sign_in("MAIN")
    sec_token, sec_name = sign_in("SECONDARY (Premium)")

    if main_token == sec_token or main_name == sec_name:
        sys.exit("\nBoth sign-ins look like the same account. Run again and pick the other account the second time.")

    print("\n" + "=" * 70)
    print(f"Main = {main_name}    Secondary = {sec_name}")
    print("Add these 4 secrets in GitHub: repo -> Settings -> Secrets and variables")
    print("-> Actions -> New repository secret. Keep them private.\n")
    print(f"YT_CLIENT_ID            = {client.get('client_id')}")
    print(f"YT_CLIENT_SECRET        = {client.get('client_secret')}")
    print(f"MAIN_REFRESH_TOKEN      = {main_token}")
    print(f"SECONDARY_REFRESH_TOKEN = {sec_token}")
    print("=" * 70)


if __name__ == "__main__":
    main()
