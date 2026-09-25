# Reel Decoder

Point it at any Instagram account. It pulls the recent reels, transcribes every one, labels each script (hook type, topic, format, what the call to action offers, and a hook / setup / payoff / pitch tag per sentence), then plots the whole account on one dashboard so you can see which hooks actually get plays.

Live dashboard: `https://<your-username>.github.io/reel-decoder/`

## How it works

```
Actions form (account, model, limit)
  → Apify: resolve the account (search if needed), scrape recent reels
  → OpenAI: transcribe each reel's audio
  → GPT or Jev: label every script
  → commit docs/data/<handle>/<model>.json
  → deploy docs/ to GitHub Pages
```

The dashboard is a static page. It can't run scrapes itself, because that would put API keys in the browser. New accounts are decoded through the **Decode an account** workflow, and the dashboard's "+ Decode a new account" button opens that form.

## Decode an account

Actions → **Decode an account** → Run workflow:

| Input | Examples |
|---|---|
| account | `@nateherkai` or `https://www.instagram.com/nateherkai/` for that exact account. `nate herk` runs an Instagram search and takes the top result by followers (the run summary lists the candidates) |
| model | `gpt`, `jev`, or `both` (both labels the same transcripts twice, so you can compare) |
| limit | number of recent reels, 5–200 |

Transcripts are cached per account, so re-running with the other model doesn't pay for transcription again.

## Secrets and variables

Settings → Secrets and variables → Actions:

| Name | Type | Needed for |
|---|---|---|
| `APIFY_TOKEN` | secret | always |
| `OPENAI_API_KEY` | secret | always (transcription), and the `gpt` model |
| `TYPESAFE_API_KEY` | secret | the `jev` model |
| `GPT_MODEL` | variable, optional | overrides the default `gpt-5-mini` |
| `TRANSCRIBE_MODEL` | variable, optional | overrides the default `gpt-4o-mini-transcribe` |

## Rough cost per 50 reels

Apify about $0.15. Transcription about $0.10. GPT labelling a few cents. Jev a fraction of a cent. GitHub Actions and Pages are free on a public repo.

## Caveats

- Plays are Instagram's public count at scrape time, so the newest reels are still climbing.
- 50 reels is a small sample. Hook types with under 3 reels are dimmed on the dashboard.
- Scraped thumbnails and transcripts belong to the creators. Keep this for research.
