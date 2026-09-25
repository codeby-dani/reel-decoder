// GET → the five most recent decode runs, so the dashboard can show progress.
module.exports = async (req, res) => {
  const repo = process.env.GITHUB_REPO;
  const r = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/decode.yml/runs?per_page=5`, {
    headers: {
      Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
    },
  });
  if (!r.ok) return res.status(502).json({ error: `GitHub ${r.status}` });
  const { workflow_runs = [] } = await r.json();
  res.setHeader("Cache-Control", "no-store");
  res.status(200).json({
    runs: workflow_runs.map((w) => ({
      title: w.display_title,
      status: w.status,
      conclusion: w.conclusion,
      started: w.run_started_at || w.created_at,
      updated: w.updated_at,
      url: w.html_url,
    })),
  });
};
