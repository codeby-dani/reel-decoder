// GET ?run=<GitHub run id> → where a decode is: GitHub's run status plus the stage/counts
// the pipeline writes to the Apify key-value store "reel-decoder-progress".
const APIFY = "https://api.apify.com/v2";
let storeId = null; // reused while the function instance stays warm

module.exports = async (req, res) => {
  res.setHeader("Cache-Control", "no-store");
  const run = String(req.query.run || "");
  if (!/^\d{5,20}$/.test(run)) return res.status(400).json({ error: "Missing run id." });

  const gh = await fetch(`https://api.github.com/repos/${process.env.GITHUB_REPO}/actions/runs/${run}`, {
    headers: {
      Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });
  if (!gh.ok) return res.status(502).json({ error: `GitHub ${gh.status}` });
  const w = await gh.json();

  let progress = null;
  try {
    const token = process.env.APIFY_TOKEN;
    if (!storeId) {
      // get-or-create by name; idempotent
      const s = await fetch(`${APIFY}/key-value-stores?name=reel-decoder-progress&token=${token}`, { method: "POST" });
      storeId = (await s.json()).data?.id;
    }
    if (storeId) {
      const r = await fetch(`${APIFY}/key-value-stores/${storeId}/records/run-${run}?token=${token}`);
      if (r.ok) progress = await r.json();
    }
  } catch {
    progress = null; // progress is a nice-to-have; GitHub status still answers
  }

  res.status(200).json({
    id: w.id,
    title: w.display_title,
    status: w.status, // queued | in_progress | completed
    conclusion: w.conclusion, // success | failure | cancelled | null
    started: w.run_started_at || w.created_at,
    updated: w.updated_at,
    url: w.html_url,
    progress, // {stage, done, total, model, handle, models, error} or null before the pipeline starts
  });
};
