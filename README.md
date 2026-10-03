# Reel Decoder

Point it at any Instagram account. It pulls the recent reels, transcribes every one, labels each script (hook type, topic, format, what the call to action offers, and a hook / setup / payoff / pitch tag per sentence), then plots the whole account on one dashboard so you can see which hooks actually get plays.

Hosted on Vercel: the dashboard is `docs/`, and `api/` holds a few small functions.

This repo is the public half: the code and the example accounts in `docs/data/`. Decodes run in a separate **private** repo that holds the workflow and the results, so the accounts you decode don't show up here, in the Actions tab, or to visitors. The dashboard loads them through `/api/data`, which needs the passcode.

## How it works

```
Dashboard (Vercel)
  search box → /api/search → Apify Instagram search → you pick the account
  Decode     → /api/decode → starts the workflow in the private repo
  your data  → /api/data   → reads the private repo, passcode required
GitHub Actions in the private repo ("Decode an account")
  → Apify: scrape recent reels
  → OpenAI: transcribe each reel
  → GPT or Jev: label every script
  → commit data/<handle>/<model>.json in the private repo
```

The heavy work stays in GitHub Actions, because 50 reels take 10–15 minutes. Vercel keeps the API keys out of the browser, and a passcode keeps strangers from spending your credit. You can also start a run by hand in the private repo: Actions → **Decode an account** → Run workflow (account takes `@handle`, a profile link, or a name to search).

Each run records how long labelling took, the median time per reel and the cost per model. GPT and Jev label the same number of reels at a time, and GPT runs with reasoning off, so the two are comparable.

To show an account to visitors, copy its `<model>.json` and `thumbs/` from the private repo's `data/<handle>/` into `docs/data/<handle>/` here and add it to `docs/data/index.json`.

Transcripts are cached per account, so re-running with the other model doesn't pay for transcription again.

## Vercel environment variables

Project → Settings → Environment Variables:

| Name | Value |
|---|---|
| `DECODE_PASSCODE` | any passphrase; the dashboard asks for it before searching or decoding |
| `APIFY_TOKEN` | same Apify token as the GitHub secret (used for search) |
| `GITHUB_TOKEN` | fine-grained token on the **private** repo: **Actions: Read and write**, **Contents: Read** |
| `GITHUB_REPO` | `owner/reel-decoder-private` |

## GitHub secrets and variables (in the private repo)

Repo → Settings → Secrets and variables → Actions:

| Name | Type | Needed for |
|---|---|---|
| `APIFY_TOKEN` | secret | always |
| `OPENAI_API_KEY` | secret | always (transcription), and the `gpt` model |
| `TYPESAFE_API_KEY` | secret | the `jev` model, straight to TypeSafe. Used first when set |
| `AI_GATEWAY_API_KEY` | secret | the `jev` model through Vercel AI Gateway, used only when there is no TypeSafe key |
| `GPT_MODEL` | variable, optional | overrides the default `gpt-6-luna` |
| `TRANSCRIBE_MODEL` | variable, optional | overrides the default `gpt-4o-mini-transcribe` |

## Rough cost per 50 reels

Apify about $0.15. Transcription about $0.10. GPT labelling a few cents. Jev a fraction of a cent. GitHub Actions is free on a public repo; Vercel Hobby covers the dashboard. Each Instagram search is about $0.02.

## Caveats

- Plays are Instagram's public count at scrape time, so the newest reels are still climbing.
- 50 reels is a small sample. Hook types with under 3 reels are dimmed on the dashboard.
- Scraped thumbnails and transcripts belong to the creators. Keep this for research.
