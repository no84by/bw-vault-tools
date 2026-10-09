"""Thin reasoning-gateway client for the cascade digest.

The reasoning gateway is a transparent proxy to the Anthropic API that injects the subscription OAuth
token and audits per `x-agent-id`. Clients send NO credentials. Its address is supplied at runtime by
`REASONING_GATEWAY_URL` — the shipped package defaults to *no* gateway (no baked-in localhost URL), so
`summarize()` falls back to a plain digest unless a deployment opts in via the env var.
We use stdlib only (no SDK) so the cascade stays dependency-light.

The gateway is used to turn a run summary + the held-op list into a short operator digest. It does
NOT authorize any vault write — the conservative policy already decided that. If the gateway is
unreachable, `summarize()` falls back to a plain-text digest, so the cascade never depends on it.
"""
import json
import os
import urllib.request

GATEWAY_URL = (os.environ.get("REASONING_GATEWAY_URL") or "").strip()
AGENT_ID = "bw-vault-cascade"
SONNET = "claude-sonnet-4-6"


def reason(system: str, prompt: str, model: str = SONNET, max_tokens: int = 512, timeout: int = 40):
    """One-shot completion via the gateway. Returns the text, or None on any failure (caller
    must treat None conservatively — never as approval)."""
    if not GATEWAY_URL:
        return None
    body = json.dumps({"model": model, "max_tokens": max_tokens, "system": system,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        GATEWAY_URL + "/v1/messages", data=body, method="POST",
        headers={"content-type": "application/json", "anthropic-version": "2023-06-01",
                 "x-agent-id": AGENT_ID})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return "".join(b.get("text", "") for b in data.get("content", [])) or None
    except Exception:
        return None


def _plain_digest(summary: dict, held: list) -> str:
    lines = [f"bw-vault cascade: {summary.get('applied', 0)} applied, {len(held)} held."]
    for stage, detail in summary.get("stages", []):
        lines.append(f"  {stage}: {detail}")
    if held:
        lines.append("Held for review (nothing auto-resolved):")
        lines += [f"  - {label} ({reason})" for label, reason in held]
    return "\n".join(lines)


def summarize(summary: dict, held: list, reasoner=reason) -> str:
    """Operator digest of a cascade run. Uses the gateway to phrase it and flag urgency; falls back
    to a deterministic plain digest if the gateway is down (so the cascade always produces output)."""
    base = _plain_digest(summary, held)
    if not held:
        return base
    text = reasoner(
        system=("You write a terse operator digest for an unattended password-vault sync. Do not "
                "recommend auto-applying anything — held items are intentionally for human review. "
                "List the held items, group obvious ones, and flag any that look security-relevant "
                "(a password conflict, a deletion). Plain text, no preamble, under 120 words."),
        prompt=base)
    return text or base
