"""Square card payments for camp registration.

Env-gated, exactly like crypto.py: with no Square credentials configured the
whole feature is dormant — ``enabled()`` is False, the frontend never shows a
card form, and the pay endpoint returns a friendly "card payments aren't set
up" error. Drop the credentials in and it turns on. No card data ever touches
our server: the browser tokenizes the card with Square's Web Payments SDK and
sends us only a one-time ``source_id`` token, which we charge server-side.

Set these in the environment (on the VM, /etc/highergrade.env):

    SQUARE_ENV           sandbox | production   (default: sandbox)
    SQUARE_ACCESS_TOKEN  server-side secret — the access token for the app
    SQUARE_APP_ID        public — the application id (frontend SDK needs it)
    SQUARE_LOCATION_ID   public — the location to attribute payments to

APP_ID and LOCATION_ID are safe to expose to the browser (the SDK requires
them); ACCESS_TOKEN must stay server-side. Get all three from
https://developer.squareup.com/apps → your app → Credentials / Locations.
"""

import json
import os
import urllib.error
import urllib.request

# Square API version we code against. Pinned so responses stay stable.
_SQUARE_VERSION = "2025-01-23"


def _env():
    return (os.environ.get("SQUARE_ENV") or "sandbox").strip().lower()


def _access_token():
    return (os.environ.get("SQUARE_ACCESS_TOKEN") or "").strip()


def app_id():
    return (os.environ.get("SQUARE_APP_ID")
            or os.environ.get("SQUARE_APPLICATION_ID") or "").strip()


def location_id():
    return (os.environ.get("SQUARE_LOCATION_ID") or "").strip()


def enabled():
    """True only when every credential needed to charge a card is present."""
    return bool(_access_token() and app_id() and location_id())


def _api_base():
    return ("https://connect.squareupsandbox.com" if _env() != "production"
            else "https://connect.squareup.com")


def public_config():
    """Public values the browser needs to render/tokenize the card form.

    Never includes the access token. ``enabled`` lets the frontend decide
    whether to show the card option at all.
    """
    return {
        "enabled": enabled(),
        "env": "production" if _env() == "production" else "sandbox",
        "appId": app_id(),
        "locationId": location_id(),
    }


class SquareError(Exception):
    """A card charge that did not succeed, with a family-friendly message."""

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.message = message
        self.detail = detail


def create_payment(source_id, amount_cents, idempotency_key,
                   reference_id=None, note=None, verification_token=None,
                   buyer_email=None, currency="CAD"):
    """Charge a tokenized card via Square's Payments API.

    Returns the Square ``payment`` object on success. Raises ``SquareError``
    with a user-safe message on any failure (declined card, network, config).
    """
    if not enabled():
        raise SquareError("Card payments aren't set up yet. Please use another "
                          "payment option.")
    if not source_id:
        raise SquareError("The card form didn't return a payment token. Please "
                          "re-enter your card and try again.")
    if not amount_cents or amount_cents <= 0:
        raise SquareError("We couldn't determine the amount to charge. Please "
                          "refresh and try again.")

    body = {
        "source_id": source_id,
        "idempotency_key": idempotency_key,
        "amount_money": {"amount": int(amount_cents), "currency": currency},
        "location_id": location_id(),
        "autocomplete": True,
    }
    if reference_id:
        body["reference_id"] = str(reference_id)[:40]  # Square caps this at 40
    if note:
        body["note"] = str(note)[:500]
    if verification_token:                              # 3-D Secure / SCA result
        body["verification_token"] = verification_token
    if buyer_email:
        body["buyer_email_address"] = buyer_email

    req = urllib.request.Request(
        _api_base() + "/v2/payments",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Square-Version": _SQUARE_VERSION,
            "Authorization": "Bearer " + _access_token(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Square returns a JSON body with an ``errors`` array on 4xx/5xx.
        detail = None
        try:
            detail = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            pass
        raise SquareError(_friendly_error(detail), detail)
    except Exception as e:  # noqa: BLE001 — network, timeout, DNS, etc.
        raise SquareError("We couldn't reach the card processor. Please try "
                          "again in a moment.", str(e))

    payment = (data or {}).get("payment") or {}
    status = payment.get("status")
    if status not in ("COMPLETED", "APPROVED"):
        raise SquareError(_friendly_error(data)
                          or "Your card could not be processed. Please try "
                             "another card.", data)
    return payment


# Map Square's error codes to short, non-technical messages for families.
_DECLINE_MESSAGES = {
    "CARD_DECLINED": "Your card was declined. Please try another card.",
    "INSUFFICIENT_FUNDS": "The card was declined for insufficient funds.",
    "CVV_FAILURE": "The security code (CVV) didn't match. Please re-check it.",
    "ADDRESS_VERIFICATION_FAILURE": "The billing postal code didn't match your card.",
    "INVALID_EXPIRATION": "The expiry date on the card is invalid.",
    "CARD_EXPIRED": "That card has expired. Please use another card.",
    "GENERIC_DECLINE": "Your card was declined. Please try another card.",
    "CARD_DECLINED_VERIFICATION_REQUIRED": "Your bank needs to verify this "
        "payment. Please try again and complete the verification.",
}


def _friendly_error(detail):
    try:
        errors = (detail or {}).get("errors") or []
        for err in errors:
            code = err.get("code") or ""
            if code in _DECLINE_MESSAGES:
                return _DECLINE_MESSAGES[code]
        if errors:
            # Fall back to Square's human detail if present, else a generic line.
            return (errors[0].get("detail")
                    or "Your card could not be processed. Please try another card.")
    except Exception:  # noqa: BLE001
        pass
    return None
