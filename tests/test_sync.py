import json
import os
import sys

import httplib2
import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sync_likes  # noqa: E402


def http_error(status, reason):
    content = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
    return HttpError(httplib2.Response({"status": status}), content)


class Request:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, num_retries=0):
        return self.fn()


class FakeYouTube:
    """Mimics the parts of the YouTube API client we use."""

    def __init__(self, name, likes, quota=None, errors=None):
        self.name = name
        self.likes = list(likes)  # newest first, like the real API
        self.quota = quota  # number of rate() calls allowed, None = unlimited
        self.errors = errors or {}  # video_id -> HttpError
        self.rated = []

    def channels(self):
        return self

    def videos(self):
        return self

    def list(self, part, mine=None, myRating=None, maxResults=50, pageToken=None, fields=None):
        if mine:
            return Request(lambda: {"items": [{"snippet": {"title": self.name}}]})
        start = int(pageToken or 0)
        page = self.likes[start:start + maxResults]
        resp = {"items": [{"id": v, "snippet": {"title": "t-" + v}} for v in page]}
        if start + maxResults < len(self.likes):
            resp["nextPageToken"] = str(start + maxResults)
        return Request(lambda: resp)

    def rate(self, id, rating):
        def do():
            if id in self.errors:
                raise self.errors[id]
            if self.quota is not None:
                if self.quota <= 0:
                    raise http_error(403, "quotaExceeded")
                self.quota -= 1
            self.rated.append(id)
            self.likes.insert(0, id)
            return {}
        return Request(do)


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "state.json")


def run(main, sec, state_path, **kw):
    logs = []
    code = sync_likes.run(main, sec, state_path, log=logs.append, **kw)
    return code, logs


def saved(state_path):
    with open(state_path) as f:
        return json.load(f)


def test_backfill_main_to_secondary_oldest_first(state_path):
    main = FakeYouTube("Main", ["m3", "m2", "m1"])  # m1 liked first
    sec = FakeYouTube("Sec", [])
    code, _ = run(main, sec, state_path)
    assert code == 0
    assert sec.rated == ["m1", "m2", "m3"]
    assert main.rated == []
    assert set(saved(state_path)["synced"]) == {"m1", "m2", "m3"}


def test_main_likes_go_to_secondary_first(state_path):
    main = FakeYouTube("Main", ["m2", "m1"])
    sec = FakeYouTube("Sec", ["s2", "s1"])
    calls = []
    main.rate = _spy(main.rate, calls, "Main")
    sec.rate = _spy(sec.rate, calls, "Sec")
    run(main, sec, state_path)
    assert calls == [("Sec", "m1"), ("Sec", "m2"), ("Main", "s1"), ("Main", "s2")]


def _spy(fn, calls, label):
    def wrapper(id, rating):
        calls.append((label, id))
        return fn(id=id, rating=rating)
    return wrapper


def test_paging_over_many_likes(state_path):
    main = FakeYouTube("Main", [f"v{i}" for i in range(120, 0, -1)])
    sec = FakeYouTube("Sec", [])
    run(main, sec, state_path)
    assert sec.rated == [f"v{i}" for i in range(1, 121)]


def test_second_run_does_nothing(state_path):
    main = FakeYouTube("Main", ["a", "b"])
    sec = FakeYouTube("Sec", ["c"])
    run(main, sec, state_path)
    main.rated.clear()
    sec.rated.clear()
    run(main, sec, state_path)
    assert main.rated == [] and sec.rated == []


def test_unlike_is_not_re_added_or_propagated(state_path):
    main = FakeYouTube("Main", ["a", "b"])
    sec = FakeYouTube("Sec", [])
    run(main, sec, state_path)
    sec.likes.remove("a")  # user unlikes "a" on Secondary
    main.likes.remove("b")  # and "b" on Main
    sec.rated.clear()
    main.rated.clear()
    run(main, sec, state_path)
    assert sec.rated == [] and main.rated == []
    assert "a" in main.likes and "b" in sec.likes  # other side untouched


def test_unliked_on_both_then_reliked_syncs_again(state_path):
    main = FakeYouTube("Main", ["a"])
    sec = FakeYouTube("Sec", ["a"])
    run(main, sec, state_path)
    main.likes.remove("a")
    sec.likes.remove("a")
    run(main, sec, state_path)
    assert "a" not in saved(state_path)["synced"]
    sec.likes.insert(0, "a")  # liked again on Secondary
    run(main, sec, state_path)
    assert main.rated == ["a"]


def test_first_run_baseline_is_intersection(state_path):
    main = FakeYouTube("Main", ["a", "b"])
    sec = FakeYouTube("Sec", ["b", "c"])
    run(main, sec, state_path)
    assert main.rated == ["c"] and sec.rated == ["a"]
    assert set(saved(state_path)["synced"]) == {"a", "b", "c"}


def test_quota_exceeded_saves_progress_and_resumes(state_path):
    main = FakeYouTube("Main", ["m4", "m3", "m2", "m1"])
    sec = FakeYouTube("Sec", [], quota=2)
    code, logs = run(main, sec, state_path)
    assert code == 0
    assert sec.rated == ["m1", "m2"]
    assert any("quota" in line for line in logs)
    assert any("Still to go: 2" in line for line in logs)
    sec.quota = None  # next day
    run(main, sec, state_path)
    assert sec.rated == ["m1", "m2", "m3", "m4"]


def test_quota_exceeded_while_listing(state_path):
    main = FakeYouTube("Main", ["a"])
    sec = FakeYouTube("Sec", [])

    def broken(**kw):
        def fail():
            raise http_error(403, "quotaExceeded")
        return Request(fail)
    sec.list = broken
    code, _ = run(main, sec, state_path)
    assert code == 0


def test_unavailable_video_is_skipped_for_good(state_path):
    main = FakeYouTube("Main", ["b", "gone", "a"])
    sec = FakeYouTube("Sec", [], errors={"gone": http_error(404, "videoNotFound")})
    run(main, sec, state_path)
    assert sec.rated == ["a", "b"]
    assert saved(state_path)["skipped"] == ["gone"]
    sec.errors.clear()
    sec.rated.clear()
    run(main, sec, state_path)
    assert sec.rated == []


def test_other_errors_still_save_state_then_raise(state_path):
    main = FakeYouTube("Main", ["b", "a"])
    sec = FakeYouTube("Sec", [], errors={"b": http_error(500, "backendError")})
    with pytest.raises(HttpError):
        run(main, sec, state_path)
    assert "a" in saved(state_path)["synced"]


def test_dry_run_changes_nothing(state_path):
    main = FakeYouTube("Main", ["a"])
    sec = FakeYouTube("Sec", ["b"])
    code, logs = run(main, sec, state_path, dry_run=True)
    assert code == 0
    assert main.rated == [] and sec.rated == []
    assert not os.path.exists(state_path)
    assert any("Main account:      Main" in line for line in logs)


def test_latest_copies_newest_main_likes_only_and_never_touches_main(state_path):
    main = FakeYouTube("Main", [f"m{i}" for i in range(10, 0, -1)])  # m10 newest
    sec = FakeYouTube("Sec", ["m1", "s1"])  # m1 already there, s1 is Secondary-only
    code, _ = run(main, sec, state_path, latest=3)
    assert code == 0
    assert sec.rated == ["m8", "m9", "m10"]  # newest 3 missing, oldest first
    assert sec.likes[0] == "m10"  # Main's latest like ends up on top
    assert main.rated == []  # nothing is written to Main


def test_limit(state_path):
    main = FakeYouTube("Main", ["c", "b", "a"])
    sec = FakeYouTube("Sec", [])
    run(main, sec, state_path, limit=2)
    assert sec.rated == ["a", "b"]


def test_logs_hide_video_ids_unless_verbose(state_path):
    main = FakeYouTube("Main", ["secret-id"])
    sec = FakeYouTube("Sec", [])
    _, logs = run(main, sec, state_path)
    assert not any("secret-id" in line for line in logs)


def test_missing_env_vars(monkeypatch, capsys):
    for name in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "MAIN_REFRESH_TOKEN", "SECONDARY_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert sync_likes.main([]) == 2
    assert "Missing environment variables" in capsys.readouterr().err
