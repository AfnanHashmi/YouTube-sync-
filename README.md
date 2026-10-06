# YouTube likes sync (Main ⇄ Secondary)

A temporary setup for two YouTube accounts:

- **Main → Secondary:** every video liked on Main gets liked on Secondary, in the same order.
- **Secondary → Main:** new videos you like on Secondary also get liked on Main, so Main stays complete for when you switch back.

It runs on GitHub Actions **only when you click Run workflow** (there is no daily schedule). By default one run copies Main's **newest 190 likes** to Secondary (one-way), the most you can do per day under Google's free quota. Clear the **latest** box to do the full two-way sync instead.

> Unlikes are never copied. If you unlike a video on one account, the tool leaves the other account alone and won't re-add the like.

---

## One-time setup (about 15 minutes)

You need a computer with Python 3.9+ for step 4 only.

### 1. Create a Google Cloud project
Use **either** Google account for this; Main is a good choice.
1. Go to <https://console.cloud.google.com/> and create a new project, e.g. `youtube-sync`.
2. Open **APIs & Services → Library**, search for **YouTube Data API v3** and click **Enable**.

### 2. Set up the sign-in screen (OAuth consent)
1. Open **APIs & Services → OAuth consent screen** (also called **Google Auth Platform**). Click **Get started**.
2. App name: anything, e.g. `My likes sync`. User support email: your email. Audience: **External**. Finish.
3. Open **Audience** and click **Publish app** → confirm. The status should say **In production**.
   - This step is important. If the app stays in "Testing", Google logs it out after 7 days and the sync stops working.
   - There's no review needed for personal use. When you sign in you'll see *"Google hasn't verified this app"*: click **Advanced → Go to … (unsafe)**. It's your own app.

### 3. Create the client and download it
1. Open **Clients** (or **Credentials → Create credentials → OAuth client ID**).
2. Application type: **Desktop app** → Create.
3. Click **Download JSON** and save the file as `client_secret.json` in this project folder.

### 4. Sign in to both accounts (on your computer)
```bash
git clone https://github.com/AfnanHashmi/YouTube-sync-.git
cd YouTube-sync-
# put client_secret.json in this folder
pip install -r requirements.txt
python get_tokens.py
```
A browser opens twice: first sign in as **Main**, then as **Secondary**. If your account has several channels, pick the right one. At the end, the script prints the channel names (check they're right) and **4 values**.

### 5. Add the 4 values as GitHub secrets
In GitHub, open this repo → **Settings → Secrets and variables → Actions → New repository secret**, and add each one:

| Name | Value |
|---|---|
| `YT_CLIENT_ID` | printed by `get_tokens.py` |
| `YT_CLIENT_SECRET` | printed by `get_tokens.py` |
| `MAIN_REFRESH_TOKEN` | printed by `get_tokens.py` |
| `SECONDARY_REFRESH_TOKEN` | printed by `get_tokens.py` |

Don't share these values with anyone, and don't commit `client_secret.json`. It's already in `.gitignore`.

### 6. Test it
1. Open the repo's **Actions** tab → **Sync likes** → **Run workflow**. Tick **Dry run** → Run.
2. Open the run's log and check that:
   - `Main account:` and `Secondary account:` show the right channels
   - the "Liked on Main" count roughly matches what YouTube shows
3. Run it again with **Dry run** unticked and **latest** `190` (the default). Main's 190 newest likes appear in Secondary's **Liked videos**, with Main's latest like on top.
4. You're done. Nothing runs by itself; click **Run workflow** again whenever you want another batch.

---

## Good to know

- **Big like collections take a few days.** Google's free API quota allows about **190 likes per day**, shared between both directions. For example, 2,000 likes take about 11 days. Each run continues where the last one stopped, and the log shows how many are left. In full two-way mode, Main's likes are copied to Secondary first; likes made on Secondary are copied to Main after that.
- **Keep the repo private.** The Actions logs show your two channel names (no video titles, unless you run `sync_likes.py --verbose` yourself).
- **GitHub pauses scheduled jobs after 60 days with no activity in the repo.** You'll get an email; re-enable the workflow from the Actions tab.
- **If the log says the login expired**, run `python get_tokens.py` again and update the two `*_REFRESH_TOKEN` secrets.
- Deleted or private videos can't be liked. They're skipped and not retried.
- The sync state (which videos are already synced) is kept in the GitHub Actions cache, not in the repo.

## Running it locally (optional)
```bash
export YT_CLIENT_ID=... YT_CLIENT_SECRET=... MAIN_REFRESH_TOKEN=... SECONDARY_REFRESH_TOKEN=...
python sync_likes.py --dry-run --verbose   # list what would change, with titles
python sync_likes.py --limit 10            # like at most 10 videos
python sync_likes.py --latest 190          # one-way: Main's newest 190 missing likes -> Secondary
```
Running locally uses its own `state.json`, separate from the one GitHub uses. That's fine, because already-synced videos are detected either way.

Tests: `pip install pytest && python -m pytest`

## Turning it off (when you're back on Main with Premium)
1. GitHub → **Actions → Sync likes → ⋯ → Disable workflow** (or delete the repo).
2. Remove the app's access to both accounts at <https://myaccount.google.com/permissions>. Do this once signed in as each account.
3. Optional: delete the Google Cloud project.

Your likes stay where they are. Nothing gets unliked.
