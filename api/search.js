// POST {q} → Instagram accounts matching q, via Apify's Instagram search scraper.
const authorized = require("./_auth");

module.exports = async (req, res) => {
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST." });
  if (!authorized(req, res)) return;
  const q = String(req.body?.q || "").trim().slice(0, 80);
  if (!q) return res.status(400).json({ error: "Type a name or handle to search." });

  const url = `https://api.apify.com/v2/acts/apify~instagram-search-scraper/run-sync-get-dataset-items?token=${process.env.APIFY_TOKEN}`;
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ search: q, searchType: "user", searchLimit: 8 }),
  });
  if (!r.ok) return res.status(502).json({ error: `Instagram search failed (Apify ${r.status}).` });

  const users = (await r.json())
    .filter((u) => u.username)
    .map((u) => ({
      username: u.username.toLowerCase(),
      fullName: u.fullName || "",
      followers: u.followersCount ?? null,
      verified: !!u.verified,
      pic: u.profilePicUrl || "",
    }))
    .sort((a, b) => (b.followers || 0) - (a.followers || 0));
  res.status(200).json({ users });
};
