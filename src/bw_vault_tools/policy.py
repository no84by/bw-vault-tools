"""Conservative approval policy for the autonomous cascade.

The principle: the cascade applies, unattended, ONLY operations that provably cannot lose data.
Everything that could overwrite or delete is HELD and surfaced for a human — never guessed. This
mirrors the lesson from the manual run: when in doubt, stop and ask.

bw-vault-tools already auto-applies its "safe" ops (additive creates, one-sided edits, lossless
field-union merges) WITHOUT calling an approver. The approver is invoked only for the *gated*
(destructive) ops, so the policy here decides those:

- dedup ops (MergeOp / DeleteOp / ClearPersonalDupOp) are provably loss-free — a dedup MergeOp
  unions every field, a DeleteOp removes a byte-identical duplicate, a ClearPersonalDupOp drops a
  personal copy whose data is retained in the org. These auto-apply.
- any *sync* gated op (a password/scalar conflict, a cross-vault delete, an ambiguous case) can
  lose or overwrite real data. These are always HELD — the cascade NEVER auto-resolves them.
"""

LOSS_FREE_DEDUP_OPS = {"MergeOp", "DeleteOp", "ClearPersonalDupOp"}


def op_label(op) -> str:
    """Human-readable one-liner for a held/applied op (no secrets)."""
    name = type(op).__name__
    item = getattr(op, "item", None) or getattr(op, "payload", None) or {}
    title = (item.get("name") if isinstance(item, dict) else None) \
        or getattr(op, "item_name", None) or getattr(op, "keep_id", None) or "?"
    kind = getattr(op, "kind", name)
    target = getattr(op, "target", "")
    return f"{kind}{('->' + target) if target else ''}: {title}"


def make_approver(held):
    """Return an approver(op) -> bool. `held` is a list it appends (label, reason) to for review."""
    def approve(op) -> bool:
        if type(op).__name__ in LOSS_FREE_DEDUP_OPS:
            return True
        held.append((op_label(op), "destructive/ambiguous — held for human review"))
        return False
    return approve
