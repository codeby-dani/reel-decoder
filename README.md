# Reel Decoder

Point it at any Instagram account. It pulls the recent reels, transcribes every one, labels each script (hook type, topic, format, what the call to action offers, and a hook / setup / payoff / pitch tag per sentence), then plots the whole account on one dashboard so you can see which hooks actually get plays.

Hosted on Vercel: the dashboard is `docs/`, and `api/` holds three small functions.

## How it works

```
Dashboard (Vercel)
  search box → /api/search → Apify Instagram search → you pick the account
  Decode     → /api/decode → starts the GitHub workflow
GitHub Actions ("Decode an account")
  → Apify: scrape recent reels
  → OpenAI: transcribe each reel
  → GPT or Jev: label every script
  → commit docs/data/<handle>/<model>.json → Vercel redeploys
```

The heavy work stays in GitHub Actions, because 50 reels take 10–15 minutes. Vercel keeps the API keys out of the browser, and a passcode keeps strangers from spending your credit. You can also start a run by hand: Actions → **Decode an account** → Run workflow (account takes `@handle`, a profile link, or a name to search).

Transcripts are cached per account, so re-running with the other model doesn't pay for transcription again.

## Vercel environment variables

Project → Settings → Environment Variables:

| Name | Value |
|---|---|
| `DECODE_PASSCODE` | any passphrase; the dashboard asks for it before searching or decoding |
| `APIFY_TOKEN` | same Apify token as the GitHub secret (used for search) |
| `GITHUB_TOKEN` | fine-grained token: only this repo, **Actions: Read and write** |
| `GITHUB_REPO` | `owner/reel-decoder` |

## GitHub secrets and variables

Repo → Settings → Secrets and variables → Actions:

| Name | Type | Needed for |
|---|---|---|
| `APIFY_TOKEN` | secret | always |
| `OPENAI_API_KEY` | secret | always (transcription), and the `gpt` model |
| `AI_GATEWAY_API_KEY` | secret | the `jev` model, through Vercel AI Gateway (recommended while TypeSafe signups are paused) |
| `TYPESAFE_API_KEY` | secret | the `jev` model directly, if you have a TypeSafe account |
| `GPT_MODEL` | variable, optional | overrides the default `gpt-5-mini` |
| `TRANSCRIBE_MODEL` | variable, optional | overrides the default `gpt-4o-mini-transcribe` |

## Rough cost per 50 reels

Apify about $0.15. Transcription about $0.10. GPT labelling a few cents. Jev a fraction of a cent. GitHub Actions is free on a public repo; Vercel Hobby covers the dashboard. Each Instagram search is about $0.02.

## Caveats

- Plays are Instagram's public count at scrape time, so the newest reels are still climbing.
- 50 reels is a small sample. Hook types with under 3 reels are dimmed on the dashboard.
- Scraped thumbnails and transcripts belong to the creators. Keep this for research.
