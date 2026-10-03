"""Decode an Instagram account: scrape recent reels, transcribe them, label every script.

    python pipeline/decode.py --account "@nateherkai" --model gpt --limit 50

--account  "@handle" or a profile link = that exact account.
           Anything else (e.g. "nate herk") = Instagram search, top result by followers.
--model    gpt | jev | both

Env: APIFY_TOKEN, OPENAI_API_KEY (always, for transcription),
     TYPESAFE_API_KEY or AI_GATEWAY_API_KEY (for jev).
Writes docs/data/<handle>/<model>.json, thumbs, a transcript cache, and docs/data/index.json.
"""
import argparse, datetime, io, json, os, pathlib, re, statistics, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor

import openai
import requests
from apify_client import ApifyClient
from openai import OpenAI
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The workflow in the private repo points this at its own data/ folder; the default is the public example data.
DATA = pathlib.Path(os.environ.get("DECODE_DATA_DIR") or ROOT / "docs" / "data")
GPT_MODEL = os.environ.get("GPT_MODEL") or "gpt-6-luna"
# Jev doesn't reason before answering, so GPT runs with reasoning off to keep the timing comparable.
# Set GPT_REASONING=default to leave the model's own setting.
GPT_REASONING = os.environ.get("GPT_REASONING") or "none"
# Used when the OpenAI account can't access GPT_MODEL (e.g. gpt-5 models need a verified organization).
GPT_FALLBACK = "gpt-4.1-mini"
_fallback_lock = threading.Lock()
TRANSCRIBE_MODEL = os.environ.get("TRANSCRIBE_MODEL") or "gpt-4o-mini-transcribe"
# Jev goes to TypeSafe directly when TYPESAFE_API_KEY is set, else through Vercel AI Gateway
# (the gateway returned 429/503 on 6 of 50 reels in the 25 Sep run).
if os.environ.get("TYPESAFE_API_KEY"):
    JEV_URL, JEV_KEY_ENV = "https://api.typesafe.ai/v1/systemone", "TYPESAFE_API_KEY"
    JEV_MODEL = os.environ.get("JEV_MODEL") or "jev-latest"
else:
    JEV_URL, JEV_KEY_ENV = "https://ai-gateway.vercel.sh/typesafe/v1/systemone", "AI_GATEWAY_API_KEY"
    JEV_MODEL = os.environ.get("JEV_MODEL") or "typesafe-ai/jev"
MAX_SENTENCES = 40
WORKERS = 8        # reels transcribed at the same time
LABEL_WORKERS = 4  # reels labelled at the same time, the same for GPT and Jev so the times compare
# USD per 1M tokens (input, output), from the Vercel AI Gateway model list on 3 Oct 2026.
PRICES = {"jev": (0.042, 0.0), "gpt-6-luna": (0.10, 0.50), "gpt-6-sol": (2.00, 10.00), "gpt-6.1-sol": (2.00, 10.00),
          "gpt-5.6-luna": (0.20, 1.20), "gpt-5-mini": (0.25, 2.00), "gpt-4.1-mini": (0.40, 1.60)}
PROGRESS_STORE = "reel-decoder-progress"  # Apify key-value store the dashboard reads

# The taxonomy. Keys are the labels; values are the definitions both models see.
HOOKS = {
    "Capability unlock": "Opens by announcing something you can now do (\"You can now...\", \"You can turn X into Y\").",
    "Breaking news": "Opens with a release, launch, leak or announcement that just happened (\"X just dropped\", \"X is here\").",
    "Hot take": "Opens with a contrarian claim, a warning or a challenge (\"Stop doing X\", \"You're doing X wrong\", \"X just killed Y\").",
    "Personal experiment": "Opens with something the creator tried or did themselves (\"I just gave...\", \"I asked...\", \"I cloned...\").",
    "Money angle": "Opens with an income, revenue or side-hustle promise (\"$10K a month\", \"easiest way to make money\").",
    "Someone built": "Opens by pointing at something another person or company built or released (\"Someone just built...\").",
    "How-to / explainer": "Opens by promising to teach or explain (\"Here's how to...\", \"Let me explain X in 60 seconds\").",
    "Other": "Music, skits, no speech, or an opener that fits none of the above.",
}
TOPICS = {
    "AI design & websites": "Designing or building websites, UI, front-end with AI.",
    "AI video & content": "Making videos, images, shorts or social content with AI.",
    "Model launches": "A new AI model or model release and what it can do.",
    "AI coding & skills": "Coding agents, Claude Code / Codex, skills, plugins, cloning software.",
    "Agents & personal OS": "AI assistants, agents, second brains, personal operating systems.",
    "Free tools & cost cuts": "Free or cheaper alternatives, free APIs, open-source replacements.",
    "Prompting & methods": "Prompts, instructions, frameworks and methods for using AI better.",
    "Make money with AI": "Side hustles, selling AI services, business income.",
    "Trading bots": "AI trading stocks or crypto.",
    "Sales & outreach": "Lead generation, cold outreach, CRM, sales pipelines.",
    "Other": "Anything else.",
}
FORMATS = {
    "Tool demo": "Introduces one tool and what it does.",
    "Screen demo": "The creator shows their own screen or build working.",
    "Step-by-step": "Walks through numbered or sequential steps.",
    "Listicle": "A list of N tools, tips or things.",
    "Framework": "Explains a mental model or method with named parts.",
    "News explainer": "Explains a piece of news and why it matters.",
    "Showcase": "Shows off results or examples, mostly visual.",
    "Talking head": "Opinion or advice spoken to camera without a demo.",
    "Music / visual only": "No meaningful speech.",
}
ASKS = {
    "Skill / repo": "Comment to get a skill, GitHub repo or code.",
    "Setup guide": "Comment to get a setup guide, process or workflow doc.",
    "Tool link": "Comment to get a link to a third-party tool.",
    "List": "Comment to get the full list.",
    "Full video": "Comment to get a longer video or course.",
    "Prompt pack": "Comment to get prompts or a leaked prompt.",
    "Source asset": "Comment to get a source file or asset.",
    "Follow": "Asks to follow or stay tuned, no comment keyword.",
    "None": "No call to action.",
}
ROLES = {
    "hook": "Opening line(s) meant to stop the scroll.",
    "setup": "Context, problem or how it works.",
    "payoff": "The result, proof, demo outcome or key benefit.",
    "pitch": "The call to action: comment, follow, link.",
}


# ---------- progress (read by the dashboard through /api/progress) ----------

class Progress:
    """Writes {stage, done, total, ...} to an Apify key-value record. Stage changes are written right away;
    per-reel ticks are flushed by a background thread every 0.5s, so labelling never waits on a write."""

    def __init__(self, apify: ApifyClient):
        self.run_id = os.environ.get("GITHUB_RUN_ID")
        self.state, self.dirty, self.lock = {"stage": "starting"}, False, threading.Lock()
        self.kv = None
        if self.run_id:
            try:
                self.kv = apify.key_value_store(apify.key_value_stores().get_or_create(name=PROGRESS_STORE).id)
                threading.Thread(target=self._flush_loop, daemon=True).start()
            except Exception as e:
                print(f"  progress disabled: {e}")

    def _write(self):
        with self.lock:
            state, self.dirty = dict(self.state), False
        try:
            self.kv.set_record(f"run-{self.run_id}", state)
        except Exception as e:
            print(f"  progress write failed: {e}")

    def _flush_loop(self):
        while True:
            time.sleep(0.5)
            if self.dirty:
                self._write()

    def update(self, force=False, **fields):
        with self.lock:
            self.state.update(fields, updated=time.time())
            self.dirty = True
        if self.kv and force:
            self._write()

    def stage(self, name, **fields):
        self.update(force=True, stage=name, done=0, **fields)

    def tick(self):
        with self.lock:
            self.state["done"] = self.state.get("done", 0) + 1
            self.dirty = True


# ---------- account + scraping ----------

def run_items(apify: ApifyClient, run) -> list[dict]:
    """Dataset items of a finished actor run. apify-client 3 returns a Run model, older versions a dict."""
    if run is None:
        sys.exit("The Apify run didn't return. Check APIFY_TOKEN and your Apify credit.")
    ds = run["defaultDatasetId"] if isinstance(run, dict) else run.default_dataset_id
    return list(apify.dataset(ds).iterate_items())


def rank_users(users: list[dict], query: str) -> list[dict]:
    """Apify mixes in unrelated big accounts, so rank by name match first, then verified, then followers."""
    norm = lambda x: re.sub(r"[^a-z0-9]", "", str(x or "").lower())
    tokens = [norm(t) for t in re.split(r"[\s@._-]+", query.lower()) if norm(t)]
    def score(u):
        hay = norm(u.get("username")) + " " + norm(u.get("fullName"))
        return sum(t in hay for t in tokens) / (len(tokens) or 1)
    users = [u for u in users if u.get("username") and score(u) > 0]
    return sorted(users, key=lambda u: (score(u), bool(u.get("verified")), u.get("followersCount") or 0), reverse=True)


def resolve_handle(account: str, apify: ApifyClient) -> str:
    account = account.strip()
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", account)
    if m and m.group(1).lower() not in {"reel", "reels", "p", "explore", "stories"}:
        return m.group(1).lower()
    if account.startswith("@"):
        return account[1:].lower()
    print(f"Searching Instagram for '{account}'...")
    run = apify.actor("apify/instagram-search-scraper").call(
        run_input={"search": re.sub(r"[^\w\s]+", " ", account).strip(), "searchType": "user", "searchLimit": 5})
    users = run_items(apify, run)
    users = rank_users(users, account)
    if not users:
        sys.exit(f"No Instagram account matching '{account}'. Try '@handle' or a profile link.")
    summary("### Search results for `%s`\n" % account + "\n".join(
        f"- @{u['username']} · {u.get('fullName', '')} · {u.get('followersCount', '?')} followers"
        for u in users[:5]) + f"\n\nPicked **@{users[0]['username']}**.\n")
    return users[0]["username"].lower()


def scrape_reels(handle: str, limit: int, apify: ApifyClient) -> list[dict]:
    print(f"Scraping {limit} reels from @{handle}...")
    run = apify.actor("apify/instagram-reel-scraper").call(
        run_input={"username": [handle], "resultsLimit": limit})
    items = run_items(apify, run)
    reels = [i for i in items if i.get("shortCode") and i.get("videoUrl")]
    if not reels:
        sys.exit(f"@{handle} returned no reels (private account, wrong handle, or no reels).")
    return reels


def save_thumb(url: str, path: pathlib.Path):
    if path.exists() or not url:
        return
    try:
        img = Image.open(io.BytesIO(requests.get(url, timeout=30).content)).convert("RGB")
        img.thumbnail((180, 320))
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, "JPEG", quality=70)
    except Exception as e:
        print(f"  thumb failed: {e}")


# ---------- transcription ----------

def transcribe(video_url: str, oa: OpenAI) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        mp4, mp3 = pathlib.Path(tmp, "v.mp4"), pathlib.Path(tmp, "a.mp3")
        mp4.write_bytes(requests.get(video_url, timeout=120).content)
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                                "stream=index", "-of", "csv=p=0", str(mp4)], capture_output=True, text=True)
        if not probe.stdout.strip():
            return ""  # the reel has no audio track, so there is nothing to transcribe
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp4), "-vn",
                        "-ac", "1", "-ar", "16000", "-b:a", "48k", str(mp3)], check=True)
        with open(mp3, "rb") as f:
            return oa.audio.transcriptions.create(model=TRANSCRIBE_MODEL, file=f).text.strip()


def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    return [s for s in re.split(r"(?<=[.!?])\s+", text) if s][:MAX_SENTENCES]


def script_block(sents: list[str], caption: str) -> str:
    lines = "\n".join(f"[{i}] {s}" for i, s in enumerate(sents)) or "(no speech)"
    return f"Instagram reel script, one numbered sentence per line:\n{lines}\n\nCaption: {caption[:500]}"


# ---------- labelling ----------

def label_gpt(sents, caption, oa: OpenAI) -> dict:
    enum = lambda d: {"type": "string", "enum": list(d)}
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["hook", "topic", "format", "ask", "roles"],
        "properties": {"hook": enum(HOOKS), "topic": enum(TOPICS), "format": enum(FORMATS),
                       "ask": enum(ASKS), "roles": {"type": "array", "items": enum(ROLES)}},
    }
    defs = "\n\n".join(f"{name}:\n" + "\n".join(f"- {k}: {v}" for k, v in d.items())
                       for name, d in [("hook", HOOKS), ("topic", TOPICS), ("format", FORMATS),
                                       ("ask", ASKS), ("roles", ROLES)])
    global GPT_MODEL
    model = GPT_MODEL
    request = dict(
        messages=[{"role": "system", "content":
                   "You label short-form video scripts. Classify the hook by the FIRST sentence only. "
                   f"Return one role per numbered sentence, in order ({len(sents)} roles).\n\n{defs}"},
                  {"role": "user", "content": script_block(sents, caption)}],
        response_format={"type": "json_schema",
                         "json_schema": {"name": "labels", "strict": True, "schema": schema}})
    if GPT_REASONING != "default":
        request["reasoning_effort"] = GPT_REASONING
    t = time.perf_counter()
    try:
        res = oa.chat.completions.create(model=model, **request)
    except openai.NotFoundError as e:
        if model == GPT_FALLBACK:
            raise
        with _fallback_lock:
            if GPT_MODEL != GPT_FALLBACK:
                summary(f"⚠️ `{GPT_MODEL}` isn't available on this OpenAI account ({e.message[:160]}). "
                        f"Using `{GPT_FALLBACK}` instead.")
                GPT_MODEL = GPT_FALLBACK
        model = GPT_FALLBACK
        request.pop("reasoning_effort", None)  # the fallback isn't a reasoning model
        t = time.perf_counter()
        res = oa.chat.completions.create(model=model, **request)
    ms = (time.perf_counter() - t) * 1000
    out = json.loads(res.choices[0].message.content)
    roles = (out["roles"] + ["payoff"] * len(sents))[:len(sents)]
    u = res.usage
    return {**out, "roles": roles, "ms": ms, "retries": 0, "price_key": model,
            "in_tok": u.prompt_tokens if u else 0, "out_tok": u.completion_tokens if u else 0}


def label_jev(sents, caption, key: str) -> dict:
    q = {
        "hook": {"type": "choice", "criteria": HOOKS,
                 "instructions": "Which hook type does the FIRST sentence [0] use?"},
        "topic": {"type": "choice", "criteria": TOPICS, "instructions": "What is this reel mainly about?"},
        "format": {"type": "choice", "criteria": FORMATS, "instructions": "What format is this reel?"},
        "ask": {"type": "choice", "criteria": ASKS,
                "instructions": "What does the viewer get for acting on the call to action?"},
    }
    for i in range(len(sents)):
        q[f"s{i}"] = {"type": "choice", "criteria": ROLES,
                      "instructions": f"What role does sentence [{i}] play in the script?"}
    body = {"state": script_block(sents, caption), "model": JEV_MODEL, "questions": q}
    for attempt in range(5):  # Jev returns 429/503/520 under load; back off 2, 4, 8, 16s
        t = time.perf_counter()
        r = requests.post(JEV_URL, timeout=60, headers={"Authorization": f"Bearer {key}"}, json=body)
        ms = (time.perf_counter() - t) * 1000  # the answering call only; back-off waits show up in wall time
        if r.status_code not in (429, 500, 502, 503, 504, 520, 522, 524):
            break
        time.sleep(2 ** (attempt + 1))
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code} from {JEV_URL}: {r.text[:300]}")
    a = r.json()["answers"]
    return {"hook": a["hook"]["choice"], "topic": a["topic"]["choice"],
            "format": a["format"]["choice"], "ask": a["ask"]["choice"],
            "roles": [a[f"s{i}"]["choice"] for i in range(len(sents))],
            "confidence": round(a["hook"].get("confidence", 0), 2),
            "ms": ms, "retries": attempt, "price_key": "jev",
            "in_tok": r.json().get("usage", {}).get("input_tokens", 0),
            "out_tok": r.json().get("usage", {}).get("output_tokens", 0)}


# ---------- output ----------

def summary(md: str):
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md + "\n")


def update_index(handle: str, full_name: str, model: str, n: int):
    path = DATA / "index.json"
    index = json.loads(path.read_text()) if path.exists() else {"accounts": []}
    acc = next((a for a in index["accounts"] if a["handle"] == handle), None)
    if not acc:
        acc = {"handle": handle, "models": []}
        index["accounts"].append(acc)
    acc.update(full_name=full_name, reels=n, updated=datetime.date.today().isoformat())
    acc["models"] = sorted(set(acc["models"]) | {model})
    index["accounts"].sort(key=lambda a: a["handle"])
    path.write_text(json.dumps(index, indent=1, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True)
    ap.add_argument("--model", choices=["gpt", "jev", "both"], default="gpt")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    models = ["gpt", "jev"] if args.model == "both" else [args.model]
    if "jev" in models and not os.environ.get(JEV_KEY_ENV):
        sys.exit("Jev needs AI_GATEWAY_API_KEY (Vercel) or TYPESAFE_API_KEY. Add one as a repo secret or pick gpt.")
    apify, oa = ApifyClient(os.environ["APIFY_TOKEN"]), OpenAI()

    progress = Progress(apify)
    progress.stage("search" if not re.match(r"@|https?://", args.account.strip()) else "scrape",
                   account=args.account, models=models)
    try:
        run(args, models, apify, oa, progress)
    except BaseException as e:
        progress.stage("failed", error=str(e)[:300])
        raise
    progress.stage("done")


def run(args, models, apify, oa, progress):
    handle = resolve_handle(args.account, apify)
    progress.stage("scrape", handle=handle)
    reels = scrape_reels(handle, min(max(args.limit, 5), 200), apify)
    folder = DATA / handle
    cache_path = folder / "transcripts.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}

    todo = [r for r in reels if r["shortCode"] not in cache]
    progress.stage("transcribe", total=len(reels))
    print(f"Transcribing {len(todo)} reels ({len(reels) - len(todo)} cached), {WORKERS} at a time...")
    t0 = time.time()

    def fetch(r):
        code = r["shortCode"]
        save_thumb(r.get("displayUrl"), folder / "thumbs" / f"{code}.jpg")
        if code in cache:
            progress.tick()
            return
        try:
            cache[code] = transcribe(r["videoUrl"], oa)
        except Exception as e:
            print(f"  transcribe failed for {code}: {str(e)[:200]}")  # not cached, so the next run retries
        finally:
            progress.tick()

    with ThreadPoolExecutor(WORKERS) as pool:
        for i, _ in enumerate(pool.map(fetch, reels), 1):
            if i % 10 == 0 or i == len(reels):
                print(f"  {i}/{len(reels)} done")
    summary(f"Transcribed {len(todo)} reels in {time.time() - t0:.0f}s.")
    folder.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))

    broken = []
    for model in models:
        out, labs, failed, first_error = [], [], 0, None
        t0 = time.time()

        def label(r):
            sents = sentences(cache.get(r["shortCode"], ""))
            caption = r.get("caption") or ""
            try:
                return r, sents, (label_gpt(sents, caption, oa) if model == "gpt" else
                                  label_jev(sents, caption, os.environ[JEV_KEY_ENV])), None
            except Exception as e:
                return r, sents, None, e

        progress.stage("label", model=model, total=len(reels))

        def label_and_tick(r):
            result = label(r)
            progress.tick()
            return result

        with ThreadPoolExecutor(LABEL_WORKERS) as pool:
            results = list(pool.map(label_and_tick, reels))
        progress.update(force=True)  # show the full count before the next stage resets it
        retry = [i for i, res in enumerate(results) if res[3]]
        second_pass = len(retry)
        if retry:  # one more, slower pass for reels that hit rate limits
            print(f"  retrying {len(retry)} reels one at a time...")
            time.sleep(5)
            for i in retry:
                results[i] = label(results[i][0])
        for r, sents, lab, err in results:
            code, caption = r["shortCode"], r.get("caption") or ""
            if err:
                print(f"  {model} failed on {code}: {err}")
                failed += 1
                first_error = first_error or str(err)
                continue
            labs.append(lab)
            words = sum(len(s.split()) for s in sents)
            out.append({
                "shortCode": code, "timestamp": r.get("timestamp"),
                "plays": r.get("videoPlayCount") or r.get("videoViewCount") or 0,
                "likes": r.get("likesCount") or 0, "comments": r.get("commentsCount") or 0,
                "duration": round(r.get("videoDuration") or 0, 1), "pinned": bool(r.get("isPinned")),
                "caption": caption, "hook": lab["hook"], "topic": lab["topic"],
                "format": lab["format"], "cta": lab["ask"], "confidence": lab.get("confidence"),
                "ms": round(lab["ms"]),
                "hook_line": sents[0] if words > 12 else caption[:140],
                "thumb": f"data/{handle}/thumbs/{code}.jpg",
                "segs": [[role, s] for role, s in zip(lab["roles"], sents)],
            })
        if not out:
            summary(f"❌ **{model}** labelled nothing for @{handle}. First error:\n\n```\n{first_error}\n```")
            broken.append(model)
            continue
        full_name = reels[0].get("ownerFullName") or handle
        in_tok, out_tok = sum(l["in_tok"] for l in labs), sum(l["out_tok"] for l in labs)
        price = PRICES.get(labs[0]["price_key"])
        stats = {
            "wall_s": round(time.time() - t0, 1), "workers": LABEL_WORKERS,
            "median_ms": round(statistics.median(l["ms"] for l in labs)),
            "labelled": len(out), "failed": failed,
            "retries": sum(l["retries"] for l in labs) + second_pass,
            "in_tok": in_tok, "out_tok": out_tok,
            "cost_usd": round(in_tok / 1e6 * price[0] + out_tok / 1e6 * price[1], 6) if price else None,
            "reasoning": (GPT_REASONING if labs[0]["price_key"] != GPT_FALLBACK else None) if model == "gpt" else None,
            "price_per_1m": price, "endpoint": "openai" if model == "gpt" else JEV_URL.split("/")[2],
        }
        (folder / f"{model}.json").write_text(json.dumps({
            "handle": handle, "full_name": full_name, "model": model,
            "model_id": GPT_MODEL if model == "gpt" else JEV_MODEL,
            "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "stats": stats, "reels": out}, ensure_ascii=False))
        update_index(handle, full_name, model, len(out))
        cost = f"${stats['cost_usd']:.4f}" if price else "no price on file"
        summary(f"✅ **{model}** labelled {len(out)} reels of @{handle} in {stats['wall_s']:.0f}s, "
                f"median {stats['median_ms']} ms per reel, {LABEL_WORKERS} at a time "
                f"({failed} failed, {stats['retries']} retries, {in_tok:,} in + {out_tok:,} out tokens, {cost}).")
    progress.stage("save")
    if broken:
        sys.exit(f"Labelling failed completely for: {', '.join(broken)}. See the errors above.")


if __name__ == "__main__":
    main()
