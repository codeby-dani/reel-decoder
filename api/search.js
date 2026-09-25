// Instagram account search via Apify. The actor takes ~60s, longer than a function may run,
// so this is two calls:
//   POST {q}             → starts the search, returns {runId}
//   GET  ?run=<runId>&q= → {status: "running"} or {status: "done", users}
const authorized = require("./_auth");

const APIFY = "https://api.apify.com/v2";
const FIELDS = "username,fullName,followersCount,verified,profilePicUrl,private";

// Apify mixes in unrelated "google" results (big celebrity accounts), so rank by how well
// the handle and name match the query, and only then by followers.
function rank(users, q) {
  const norm = (s) => String(s || "").toLowerCase().normalize("NFKD").replace(/[^a-z0-9]/g, "");
  const tokens = String(q).toLowerCase().split(/[\s@._-]+/).map(norm).filter(Boolean);
  const scored = users
    .filter((u) => u.username)
    .map((u) => {
      const hay = norm(u.username) + " " + norm(u.fullName);
      const score = tokens.filter((t) => hay.includes(t)).length / (tokens.length || 1);
      return {
        username: u.username.toLowerCase(),
        fullName: u.fullName || "",
        followers: u.followersCount ?? null,
        verified: !!u.verified,
        private: !!u.private,
        pic: u.profilePicUrl || "",
        score,
      };
    });
  const seen = new Set();
  return scored
    .filter((u) => u.score > 0 && !seen.has(u.username) && seen.add(u.username))
    .sort((a, b) => b.score - a.score || b.verified - a.verified || (b.followers || 0) - (a.followers || 0))
    .slice(0, 8);
}

module.exports = async (req, res) => {
  if (!authorized(req, res)) return;
  const token = process.env.APIFY_TOKEN;
  res.setHeader("Cache-Control", "no-store");

  if (req.method === "POST") {
    const q = String(req.body?.q || "").trim().slice(0, 80);
    if (!q) return res.status(400).json({ error: "Type a name or handle to search." });
    const r = await fetch(`${APIFY}/acts/apify~instagram-search-scraper/runs?token=${token}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ search: q, searchType: "user", searchLimit: 8 }),
    });
    if (!r.ok) return res.status(502).json({ error: `Couldn't start the Instagram search (Apify ${r.status}). Check APIFY_TOKEN.` });
    const { data } = await r.json();
    return res.status(200).json({ runId: data.id });
  }

  if (req.method === "GET") {
    const runId = String(req.query.run || "");
    if (!/^[A-Za-z0-9]{10,30}$/.test(runId)) return res.status(400).json({ error: "Missing search id." });
    const r = await fetch(`${APIFY}/actor-runs/${runId}?token=${token}`);
    if (!r.ok) return res.status(502).json({ error: `Couldn't check the search (Apify ${r.status}).` });
    const { data } = await r.json();
    if (["READY", "RUNNING"].includes(data.status)) return res.status(200).json({ status: "running" });
    if (data.status !== "SUCCEEDED") return res.status(502).json({ error: `Instagram search ${data.status.toLowerCase()}. Try again.` });
    const items = await (await fetch(`${APIFY}/datasets/${data.defaultDatasetId}/items?token=${token}&clean=true&fields=${FIELDS}`)).json();
    return res.status(200).json({ status: "done", users: rank(items, req.query.q || "") });
  }

  res.status(405).json({ error: "Use POST to start a search, GET to check it." });
};

module.exports.rank = rank;
