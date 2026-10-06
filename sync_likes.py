#!/usr/bin/env python3
"""Two-way sync of liked videos between two YouTube accounts.

* Main -> Secondary: every video liked on Main gets liked on Secondary
  (oldest first, so Secondary's "Liked videos" keeps Main's order).
* Secondary -> Main: new likes made on Secondary get liked on Main too.
  These go first because there are usually only a few of them.

Unlikes are never propagated and never undone: if a video that was already
liked on both accounts disappears from one of them, it is left alone.

Credentials come from environment variables (see README.md):
  YT_CLIENT_ID, YT_CLIENT_SECRET, MAIN_REFRESH_TOKEN, SECONDARY_REFRESH_TOKEN
"""

import argparse
import json
import os
import sys

SCOPES = ["https://www.googleapis.com/auth/youtube"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
DEFAULT_STATE_FILE = "state.json"

# Error reasons that mean "out of API quota for today" -> stop and resume tomorrow.
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}
# Error reasons that mean "this particular video can't be liked" -> skip it forever.
SKIP_REASONS = {"videoNotFound", "forbidden", "videoRatingDisabled", "invalidRating"}


class QuotaExceeded(Exception):
    pass


def error_reason(err):
    """Return the API error reason (e.g. 'quotaExceeded') from an HttpError."""
    try:
        body = json.loads(err.content.decode("utf-8"))
        return body["error"]["errors"][0]["reason"]
    except Exception:
        return ""


def error_status(err):
    try:
        return int(err.resp.status)
    except Exception:
        return 0


def execute(request):
    """Run an API request, turning quota errors into QuotaExceeded."""
    from googleapiclient.errors import HttpError

    try:
        return request.execute(num_retries=3)
    except HttpError as err:
        if error_reason(err) in QUOTA_REASONS:
            raise QuotaExceeded() from err
        raise


# ---------------------------------------------------------------- API helpers

def channel_title(service):
    resp = execute(service.channels().list(part="snippet", mine=True))
    items = resp.get("items") or []
    return items[0]["snippet"]["title"] if items else "(no channel)"


def fetch_likes(service):
    """Return [(video_id, title), ...] of the account's likes, newest first."""
    likes = []
    page_token = None
    while True:
        resp = execute(service.videos().list(
            part="snippet",
            myRating="like",
            maxResults=50,
            pageToken=page_token,
            fields="nextPageToken,items(id,snippet/title)",
        ))
        for item in resp.get("items", []):
            likes.append((item["id"], item.get("snippet", {}).get("title", "")))
        page_token = resp.get("nextPageToken")
        if not page_token:
            return likes


def like_video(service, video_id):
    execute(service.videos().rate(id=video_id, rating="like"))


# ---------------------------------------------------------------- state

def load_state(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {"synced": set(data.get("synced", [])), "skipped": set(data.get("skipped", []))}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({
            "version": 1,
            "synced": sorted(state["synced"]),
            "skipped": sorted(state["skipped"]),
        }, f, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- sync logic

def plan(main_likes, sec_likes, state):
    """Work out what to like where.

    main_likes / sec_likes are [(id, title)] newest first.
    Returns (to_main, to_sec, state) where to_* are [(id, title)] oldest first.
    """
    main_ids = {vid for vid, _ in main_likes}
    sec_ids = {vid for vid, _ in sec_likes}
    on_both = main_ids & sec_ids

    if state is None:
        # First run (or lost state): anything already liked on both is synced.
        state = {"synced": set(on_both), "skipped": set()}
    else:
        state["synced"] |= on_both
        # Forget videos unliked on BOTH accounts, so liking them again later syncs.
        state["synced"] &= main_ids | sec_ids

    done = state["synced"] | state["skipped"]
    to_main = [(v, t) for v, t in reversed(sec_likes) if v not in main_ids and v not in done]
    to_sec = [(v, t) for v, t in reversed(main_likes) if v not in sec_ids and v not in done]
    return to_main, to_sec, state


def apply(jobs, state, limit, log, verbose):
    """Like videos. jobs = [(service, label, [(id, title)]), ...] in priority order.

    Returns the number of likes written. Raises QuotaExceeded when out of quota.
    """
    from googleapiclient.errors import HttpError

    written = 0
    for service, label, videos in jobs:
        for vid, title in videos:
            if limit is not None and written >= limit:
                return written
            try:
                like_video(service, vid)
            except HttpError as err:
                reason = error_reason(err)
                if reason in SKIP_REASONS or error_status(err) == 404:
                    state["skipped"].add(vid)
                    log(f"  skip {vid} on {label} ({reason or error_status(err)})")
                    continue
                raise
            state["synced"].add(vid)
            written += 1
            if verbose:
                log(f"  liked on {label}: {vid} {title}")
    return written


def run(main, secondary, state_path, dry_run=False, limit=None, verbose=False, log=print):
    """Do one sync pass. Returns a process exit code."""
    state = load_state(state_path)
    try:
        log(f"Main account:      {channel_title(main)}")
        log(f"Secondary account: {channel_title(secondary)}")
        main_likes = fetch_likes(main)
        sec_likes = fetch_likes(secondary)
    except QuotaExceeded:
        log("Out of YouTube API quota for today; will continue on the next run.")
        return 0

    log(f"Liked on Main: {len(main_likes)}   Liked on Secondary: {len(sec_likes)}")
    to_main, to_sec, state = plan(main_likes, sec_likes, state)
    log(f"To like on Main (new likes from Secondary): {len(to_main)}")
    log(f"To like on Secondary (from Main):           {len(to_sec)}")

    if dry_run:
        if verbose:
            for label, videos in (("Main", to_main), ("Secondary", to_sec)):
                for vid, title in videos:
                    log(f"  would like on {label}: {vid} {title}")
        log("Dry run: nothing was changed.")
        return 0

    written = 0
    out_of_quota = False
    try:
        written = apply([(main, "Main", to_main), (secondary, "Secondary", to_sec)],
                        state, limit, log, verbose)
    except QuotaExceeded:
        out_of_quota = True
    finally:
        save_state(state_path, state)

    remaining = sum(1 for v, _ in to_main + to_sec if v not in state["synced"] | state["skipped"])
    if out_of_quota:
        log("Out of YouTube API quota for today; will continue on the next run.")
    log(f"Liked {written} video(s) this run. Still to go: {remaining}.")
    return 0


# ---------------------------------------------------------------- entry point

def build_service(refresh_token):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    parser.add_argument("--limit", type=int, help="like at most N videos this run")
    parser.add_argument("--verbose", action="store_true", help="print video IDs and titles")
    parser.add_argument("--state", default=DEFAULT_STATE_FILE, help="state file path")
    args = parser.parse_args(argv)

    missing = [name for name in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "MAIN_REFRESH_TOKEN",
                                 "SECONDARY_REFRESH_TOKEN") if not os.environ.get(name)]
    if missing:
        print("Missing environment variables: " + ", ".join(missing), file=sys.stderr)
        print("Run get_tokens.py and add the values as GitHub secrets (see README.md).", file=sys.stderr)
        return 2

    from google.auth.exceptions import RefreshError

    try:
        return run(
            build_service(os.environ["MAIN_REFRESH_TOKEN"]),
            build_service(os.environ["SECONDARY_REFRESH_TOKEN"]),
            args.state, dry_run=args.dry_run, limit=args.limit, verbose=args.verbose,
        )
    except RefreshError as err:
        print(f"Google login expired or was revoked ({err}).", file=sys.stderr)
        print("Run get_tokens.py again and update the refresh-token secrets.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
