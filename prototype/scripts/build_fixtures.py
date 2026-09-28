#!/usr/bin/env python
"""Build the synthetic benchmark corpus used to measure per-model bug
detection (precision/recall) across the pipeline.

Each fixture is a small, self-contained pull request *with bugs deliberately
injected* and a golden list of the expected bug findings (path, type,
severity, title keywords). A model that finds the injected bugs correctly has
a measurable detection rate; findings that match no injected bug count as
false positives.

Run from prototype/:   python scripts/build_fixtures.py
Writes JSON files under fixtures/.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures"


FIXTURES = [
    {
        "name": "cart-discount-rounding",
        "title": "Add discount support to the cart",
        "requirements": [
            "Prices must be exact to the cent (integer arithmetic, no floating-point drift).",
            "Applying the same discount twice must not change the total (idempotency).",
        ],
        "expected_bugs": [
            {
                "type": "logic",
                "severity": "critical",
                "path": "cart/pricing.py",
                "keywords": ["round", "trunc", "float", "cent"],
            },
            {
                "type": "edge-case",
                "severity": "minor",
                "path": "cart/pricing.py",
                "keywords": ["idempot", "double", "re-appl", "twice"],
            },
        ],
        "diff": """diff --git a/cart/pricing.py b/cart/pricing.py
--- a/cart/pricing.py
+++ b/cart/pricing.py
@@ -1,3 +1,22 @@
 def _cents(value):
-    return int(value * 100)
+    return int(round(value * 100))
+
+
+def apply_discount(price_cents: int, discount_pct: float) -> int:
+    if discount_pct < 0 or discount_pct > 100:
+        raise ValueError("discount must be between 0 and 100")
+    if discount_pct == 0:
+        return price_cents
+    fraction = 1 - discount_pct / 100
+    return int(price_cents * fraction)
diff --git a/cart/api.py b/cart/api.py
--- a/cart/api.py
+++ b/cart/api.py
@@ -10,6 +10,15 @@ from .pricing import apply_discount
 def checkout(cart: Cart, codes: list[str]) -> int:
-    total = _cents(cart.subtotal)
     for code in codes:
-        cart.add_discount(code)
-    return _cents(cart.total())
+        discount = DISCOUNTS.get(code)
+        if discount is None:
+            continue
+        cart.total = apply_discount(cart.total, discount)
+    # Retried requests re-apply the discount, so keep totals stable.
+    if cart.total > 0:
+        cart.total = apply_discount(cart.total, DISCOUNTS.get(codes[0]) or 0)
+    return cart.total
diff --git a/cart/tests/test_pricing.py b/cart/tests/test_pricing.py
--- a/cart/tests/test_pricing.py
+++ b/cart/tests/test_pricing.py
@@ -1,4 +1,16 @@
 from cart.pricing import apply_discount
 
 
 def test_discount_rounds_down():
-    assert apply_discount(999, 10) == 899
+    # 999 * 0.9 == 899.1 -> floor to 899
+    assert apply_discount(999, 10) == 899
+
+
+def test_discount_keeps_cents_exact():
+    # 100 * (1 - 33.3/100) == 66.7 -> 66 by float truncation
+    assert apply_discount(100, 33.3) == 66
+""",
    },
    {
        "name": "auth-token-rotation",
        "title": "Rotate session tokens on login",
        "requirements": [
            "Session tokens must be cryptographically random and never appear in logs.",
            "After rotation, the previous token must be invalidated immediately.",
        ],
        "expected_bugs": [
            {
                "type": "security",
                "severity": "critical",
                "path": "auth/tokens.py",
                "keywords": ["log", "plaintext", "tokentext", "secret"],
            },
            {
                "type": "security",
                "severity": "important",
                "path": "auth/session.py",
                "keywords": ["previous", "invalidate", "rotate", "old"],
            },
        ],
        "diff": """diff --git a/auth/tokens.py b/auth/tokens.py
--- a/auth/tokens.py
+++ b/auth/tokens.py
@@ -5,6 +5,24 @@ from secrets import token_urlsafe
 def issue_token(user_id: str) -> str:
-    return token_urlsafe(32)
+    token = token_urlsafe(32)
+    _store.put(user_id, token)
+    return token
+
+
+def rotate_token(user_id: str, old_token: str) -> str:
+    stored = _store.get(user_id)
+    if stored != old_token:
+        raise PermissionError("token mismatch")
+    new_token = token_urlsafe(32)
+    log.info("rotating token for user=%s old=%s new=%s",
+             user_id, old_token, new_token)
+    _store.put(user_id, new_token)
+    return new_token
diff --git a/auth/session.py b/auth/session.py
--- a/auth/session.py
+++ b/auth/session.py
@@ -18,6 +18,14 @@ from .tokens import rotate_token
 def refresh(token: str, user_id: str) -> str:
-    return rotate_token(user_id, token)
+    new_token = rotate_token(user_id, token)
+    # Keep the previous token valid for five minutes so in-flight
+    # requests do not fail during rotation.
+    _grace_cache.set(token, user_id, ttl=300)
+    return new_token
+
+
+def is_valid(token: str, user_id: str) -> bool:
+    if _grace_cache.get(token) == user_id:
+        return True
+    return _store.get(user_id) == token
diff --git a/auth/tests/test_rotation.py b/auth/tests/test_rotation.py
--- a/auth/tests/test_rotation.py
+++ b/auth/tests/test_rotation.py
@@ -1,4 +1,14 @@
 from auth.session import refresh
 
 
 def test_rotation_returns_new_token():
-    assert len(refresh("old", "u1")) == 43
+    first = refresh("old", "u1")
+    assert first != "old"
+
+
+def test_previous_token_still_valid_during_grace():
+    assert is_valid("old", "u1") is True
+""",
    },
    {
        "name": "webhook-delivery-retry",
        "title": "Add signed webhook delivery with retries",
        "requirements": [
            "Webhook delivery must implement exponential backoff with jitter.",
            "Non-retryable responses (4xx) must not be retried.",
            "Delivery attempts must be observable and must not block the event loop.",
        ],
        "expected_bugs": [
            {
                "type": "error-handling",
                "severity": "important",
                "path": "webhooks/deliver.py",
                "keywords": ["4xx", "retry", "non-retry"],
            },
            {
                "type": "error-handling",
                "severity": "important",
                "path": "webhooks/deliver.py",
                "keywords": ["backoff", "exponential", "delay"],
            },
            {
                "type": "concurrency",
                "severity": "minor",
                "path": "webhooks/deliver.py",
                "keywords": ["counter", "thread", "race", "increment"],
            },
        ],
        "diff": """diff --git a/webhooks/deliver.py b/webhooks/deliver.py
--- a/webhooks/deliver.py
+++ b/webhooks/deliver.py
@@ -3,6 +3,23 @@ import time
-from .sign import sign_payload
+from .sign import sign_payload
+import asyncio
+
+
+_attempts = 0
+
+
+async def deliver(url: str, payload: dict) -> bool:
+    global _attempts
+    body = sign_payload(payload)
+    while True:
+        resp = await _post(url, body)
+        _attempts += 1
+        if resp.status == 200:
+            return True
+        if resp.status >= 400 and resp.status < 500:
+            log.warning("dropping non-retryable %s", resp.status)
+            return False
+        time.sleep(1)
@@ -20,6 +37,9 @@ from .sign import sign_payload
 def retry_policy():
+    # fixed one-second retry for every other failure; no exponential backoff
     return {"max_attempts": 3}
diff --git a/webhooks/sign.py b/webhooks/sign.py
--- a/webhooks/sign.py
+++ b/webhooks/sign.py
@@ -1,4 +1,12 @@
 import hmac
+import hashlib
 
 
 def sign_payload(payload: dict) -> dict:
-    return payload
+    body = json.dumps(payload, sort_keys=True).encode()
+    sig = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
+    return {**payload, "X-Signature": sig}
""",
    },
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for fixture in FIXTURES:
        path = OUT / f"{fixture['name']}.json"
        path.write_text(json.dumps(fixture, indent=2), encoding="utf-8")
        print(f"wrote {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())