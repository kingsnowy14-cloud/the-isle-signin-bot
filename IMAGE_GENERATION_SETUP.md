# AI image generator setup

The dashboard image generator uses Cloudflare Workers AI with
`@cf/black-forest-labs/flux-1-schnell`.

## Railway variables

Add these variables to the same Railway service that runs IronWolf:

- `CF_ACCOUNT_ID`: your Cloudflare account ID.
- `CF_API_TOKEN`: an API token permitted to invoke Workers AI.

Do not commit either value to GitHub. Keep the token in Railway Variables.

## Free allowance

Cloudflare documents a free allocation of 10,000 Workers AI Neurons per day,
resetting at 00:00 UTC. FLUX.1 schnell is priced per 512×512 tile and step;
requests fail when the daily allocation is exhausted on the Free plan. Check
Cloudflare's current pricing before enabling this for a public server:
https://developers.cloudflare.com/workers-ai/platform/pricing/

## IronWolf image tokens

- Each Discord server has its own 200-token monthly pool.
- Premium accounts additionally have 3,000 personal tokens per month.
- Server tokens are used first; personal tokens are only used for the remaining
  cost and are never shared with another account.
- Monthly balances reset at the UTC month boundary. Unused tokens expire.
- Current internal image prices: 512×512 = 10 tokens; 768×768 = 18 tokens;
  1024×1024 = 30 tokens. The dashboard displays the exact token charge before
  generation and deducts it only after a successful response and image save.
- These are IronWolf credits, not a display of Cloudflare's dollar invoice.
  Cloudflare's own bill/free allowance is separate and must be monitored in
  the Cloudflare dashboard.

Generated images and the token ledger are stored under `DATA_DIR`, so attach
a persistent Railway Volume and keep `DATA_DIR` on that volume.
