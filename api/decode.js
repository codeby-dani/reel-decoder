// POST {handle, model, limit} → starts the "Decode an account" GitHub workflow.
const authorized = require("./_auth");

module.exports = async (req, res) => {
  if (req.method !== "POST") return res.status(405).json({ error: "Use POST." });
  if (!authorized(req, res)) return;

  const handle = String(req.body?.handle || "").trim().replace(/^@/, "").toLowerCase();
  const model = req.body?.model;
  const limit = Math.min(Math.max(parseInt(req.body?.limit, 10) || 50, 5), 200);
  if (!/^[a-z0-9._]{1,30}$/.test(handle)) return res.status(400).json({ error: "That isn't a valid Instagram handle." });
  if (!["gpt", "jev", "both"].includes(model)) return res.status(400).json({ error: "Model must be gpt, jev or both." });

  const repo = process.env.GITHUB_REPO;
  const r = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/decode.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref: "main", inputs: { account: "@" + handle, model, limit: String(limit) } }),
  });
  if (r.status !== 204) {
    const detail = await r.text();
    return res.status(502).json({ error: `GitHub refused the run (${r.status}). Check GITHUB_TOKEN and GITHUB_REPO.`, detail });
  }
  res.status(200).json({ ok: true, handle, model, limit });
};
