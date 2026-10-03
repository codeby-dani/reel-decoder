// GET ?path=<file under data/> → a decode result, the account index or a thumbnail from the private
// data repo. Needs the passcode, so decoded accounts are only visible to the owner.
const authorized = require("./_auth");

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "private, no-store");
  if (!authorized(req, res)) return;
  const path = String(req.query.path || "");
  const ok = path === "index.json" || /^[a-z0-9._]{1,30}\/(gpt|jev)\.json$/.test(path) || /^[a-z0-9._]{1,30}\/thumbs\/[A-Za-z0-9_-]{5,20}\.jpg$/.test(path);
  if (!ok || path.includes("..")) return res.status(400).json({ error: "Unknown path." });

  const r = await fetch(`https://api.github.com/repos/${process.env.GITHUB_REPO}/contents/data/${path}`, {
    headers: {
      Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github.raw",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });
  if (r.status === 404) return res.status(404).json({ error: "Not decoded yet." });
  if (!r.ok) return res.status(502).json({ error: `GitHub ${r.status}` });
  res.setHeader("Content-Type", path.endsWith(".jpg") ? "image/jpeg" : "application/json; charset=utf-8");
  res.status(200).send(Buffer.from(await r.arrayBuffer()));
};
