"""Stripe card payments for camp registration.

Env-gated, exactly like crypto.py: with no Stripe keys configured the whole
feature is dormant — ``enabled()`` is False, the frontend never shows a card
form, and the pay endpoints return a friendly "card payments aren't set up"
error. Drop the keys in and it turns on. No card data ever touches our server:
the browser collects the card with Stripe.js (the Payment Element) and confirms
it directly with Stripe; we only create the PaymentIntent and then verify its
status with Stripe server-side before unfreezing the account.

Set these in the environment (on the VM, /etc/highergrade.env):

    STRIPE_SECRET_KEY       server-side secret — sk_test_… (test) or sk_live_…
    STRIPE_PUBLISHABLE_KEY  public — pk_test_… / pk_live_… (the browser needs it)

Test vs live mode is decided by the key prefix (sk_test_ / sk_live_), so there
is no separate "environment" variable to set. Get both keys from
https://dashboard.stripe.com/apikeys (toggle "Test mode" for the test keys).

We charge the exact ``amountDue`` already computed at registration — the base
price minus whatever discount (code or staff referral) applied. Discounts are
therefore fully handled on our side; Stripe just charges the final number, so
there is NO need to create Stripe products, prices, or coupons.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

# Stripe API version we code against. Pinned so responses stay stable.
_STRIPE_VERSION = "2024-06-20"
_API_BASE = "https://api.stripe.com"


def _secret_key():
    return (os.environ.get("STRIPE_SECRET_KEY") or "").strip()


def publishable_key():
    return (os.environ.get("STRIPE_PUBLISHABLE_KEY") or "").strip()


def enabled():
    """True only when both keys needed to run a card charge are present."""
    return bool(_secret_key() and publishable_key())


def test_mode():
    return _secret_key().startswith("sk_test_")


def public_config():
    """Public values the browser needs to render the card form. Never includes
    the secret key. ``enabled`` lets the frontend decide whether to show the
    card option at all."""
    return {
        "enabled": enabled(),
        "publishableKey": publishable_key(),
        "testMode": test_mode(),
    }


class StripeError(Exception):
    """A Stripe call that did not succeed, with a family-friendly message."""

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.message = message
        self.detail = detail


def _request(method, path, form=None, idempotency_key=None):
    """Call the Stripe REST API (form-encoded, as Stripe expects). Returns the
    parsed JSON object. Raises StripeError with a user-safe message on failure."""
    if not _secret_key():
        raise StripeError("Card payments aren't set up yet. Please use another "
                          "payment option.")
    url = _API_BASE + path
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + _secret_key(),
        "Stripe-Version": _STRIPE_VERSION,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = None
        try:
            detail = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            pass
        raise StripeError(_friendly_error(detail), detail)
    except Exception as e:  # noqa: BLE001 — network, timeout, DNS, etc.
        raise StripeError("We couldn't reach the card processor. Please try "
                          "again in a moment.", str(e))


def create_payment_intent(amount_cents, rid, receipt_email=None,
                          description=None, currency="cad"):
    """Create a card-only PaymentIntent for the exact amount due. Returns the
    PaymentIntent object (id, client_secret, status, …)."""
    if not amount_cents or amount_cents <= 0:
        raise StripeError("We couldn't determine the amount to charge. Please "
                          "refresh and try again.")
    form = {
        "amount": int(amount_cents),
        "currency": currency,
        # Card only — keeps the Payment Element a simple card form and avoids
        # surfacing wallets/redirect methods we don't want here.
        "payment_method_types[]": "card",
        "metadata[rid]": rid,
        "description": description or "Camp registration",
    }
    if receipt_email:
        form["receipt_email"] = receipt_email
    # Idempotent on the registration id so double-clicks / retries within 24h
    # reuse the same PaymentIntent instead of spawning duplicates.
    return _request("POST", "/v1/payment_intents", form,
                    idempotency_key="pi_create_" + str(rid))


def retrieve_payment_intent(pi_id):
    """Fetch a PaymentIntent by id — the authoritative source of truth for
    whether it actually succeeded (never trust the client's word for it)."""
    if not pi_id:
        raise StripeError("Missing payment reference.")
    safe = urllib.parse.quote(str(pi_id), safe="")
    return _request("GET", "/v1/payment_intents/" + safe)


def _friendly_error(detail):
    try:
        err = (detail or {}).get("error") or {}
        # Card errors carry a customer-friendly message from Stripe.
        if err.get("type") == "card_error" and err.get("message"):
            return err["message"]
        msg = err.get("message")
        if msg:
            return msg
    except Exception:  # noqa: BLE001
        pass
    return "Your card could not be processed. Please try another card."
