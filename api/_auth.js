// Shared passcode check. Anything that spends Apify or OpenAI credit goes through this.
module.exports = function authorized(req, res) {
  const expected = process.env.DECODE_PASSCODE;
  if (!expected) {
    res.status(500).json({ error: "DECODE_PASSCODE is not set on Vercel." });
    return false;
  }
  if (req.headers["x-passcode"] !== expected) {
    res.status(401).json({ error: "Wrong passcode." });
    return false;
  }
  return true;
};
