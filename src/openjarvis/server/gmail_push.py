"""Gmail Pub/Sub push listener — real-time email alerting via Telegram.

Uses Google Cloud Pub/Sub streaming pull to receive Gmail push
notifications, fetches new messages via history.list, and sends
iMessage alerts for important emails.

Setup (one-time):
  1. Enable Cloud Pub/Sub API in your GCP project
  2. Create topic: gcloud pubsub topics create gmail-notifications
  3. Grant Gmail publish access:
     gcloud pubsub topics add-iam-policy-binding gmail-notifications \
       --member="serviceAccount:gmail-api-push@system.gserviceaccount.com" \
       --role="roles/pubsub.publisher"
  4. Create pull subscription:
     gcloud pubsub subscriptions create gmail-pull-sub \
       --topic=gmail-notifications
  5. Set GOOGLE_APPLICATION_CREDENTIALS or run: gcloud auth application-default login
  6. Add config to ~/.openjarvis/config.toml:
     [alerts.gmail_push]
     gcp_project = "your-project-id"
     topic = "gmail-notifications"
     subscription = "gmail-pull-sub"
     important_senders = ["boss@example.com"]
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from openjarvis.tools._brand import load_brand as _load_brand

logger = logging.getLogger(__name__)

_subscriber_future: Optional[Any] = None
_subscriber_client: Optional[Any] = None
_last_history_id: Optional[str] = None
_triage_client: Optional[Any] = None


def _get_triage_client() -> Any:
    """Return a singleton Anthropic client, creating it on first call."""
    global _triage_client
    if _triage_client is None:
        import anthropic

        _triage_client = anthropic.Anthropic(timeout=15)
    return _triage_client


_GMAIL_CREDS_PATH = Path.home() / ".openjarvis" / "connectors" / "gmail.json"
_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"

# Labels that mark an email as guaranteed junk — never alert.
_JUNK_LABELS = {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL",
                "CATEGORY_UPDATES", "CATEGORY_FORUMS"}

# Sender patterns that are never worth alerting on.
_JUNK_SENDER_RE = re.compile(
    r"(noreply|no-reply|donotreply|do-not-reply|mailer-daemon|postmaster"
    r"|notifications?@|updates?@|newsletter|marketing|promo)",
    re.IGNORECASE,
)

def _get_triage_system() -> str:
    """Build the triage system prompt, pulling company name from brand config."""
    brand = _load_brand()
    # Extract company name from brand.md '## Company' section, fallback to generic.
    company_name = "your business"
    in_company_section = False
    for line in brand.splitlines():
        if line.strip() == "## Company":
            in_company_section = True
            continue
        if in_company_section:
            if line.startswith("## "):
                break  # left the Company section without finding a value
            stripped = line.strip()
            if stripped:
                company_name = stripped.split(" — ")[0].strip()
                break
    return (
        f"You are an email triage assistant for the {company_name} business account. "
        "Decide whether this email is IMPORTANT enough to send a text message alert. "
        "IMPORTANT means: potential customer inquiries, client emails, "
        "business partnership or service requests, responses to outreach campaigns, "
        "calendar invitations, financial/billing alerts (Stripe, invoices, payments), "
        "domain/hosting issues, security alerts, or anything time-sensitive for the business.\n\n"
        "NOT important: marketing, newsletters, SaaS product updates, social media notifications, "
        "promotional offers, automated digests, shipping updates, app notifications, "
        "routine GitHub notifications, developer tool announcements, or mass emails.\n\n"
        "Reply with ONLY 'yes' or 'no'. Nothing else."
    )


# Triage prompt — built at call time from brand config.
# This prompt targets the business email account.
_TRIAGE_SYSTEM = _get_triage_system()


# -- Gmail API helper functions (match call_with_refresh pattern) ----------
# Each takes (token, ...) and returns a dict. Used via:
#   call_with_refresh(api_fn, credentials_path, *args, **kwargs)


def _api_watch(token: str, *, topic_name: str) -> Dict[str, Any]:
    """POST users.watch to register for push notifications."""
    resp = httpx.post(
        f"{_GMAIL_BASE}/watch",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "topicName": topic_name,
            "labelIds": ["INBOX"],
            "labelFilterBehavior": "INCLUDE",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _api_history(token: str, *, history_id: str) -> Dict[str, Any]:
    """GET users.history.list for messageAdded events."""
    resp = httpx.get(
        f"{_GMAIL_BASE}/history",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "startHistoryId": history_id,
            "historyTypes": "messageAdded",
            "labelId": "INBOX",
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _api_get_message(token: str, *, msg_id: str) -> Dict[str, Any]:
    """GET a message with snippet + headers for triage."""
    resp = httpx.get(
        f"{_GMAIL_BASE}/messages/{msg_id}",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "format": "metadata",
            "metadataHeaders": ["From", "Subject", "List-Unsubscribe"],
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# -- Core functions --------------------------------------------------------


def setup_gmail_watch(topic_name: str) -> Optional[str]:
    """Register a Gmail push watch. Returns the starting historyId."""
    try:
        from openjarvis.connectors.google_auth import call_with_refresh

        data = call_with_refresh(
            _api_watch,
            str(_GMAIL_CREDS_PATH),
            topic_name=topic_name,
        )
        history_id = data.get("historyId")
        expiration = data.get("expiration")
        logger.info(
            "Gmail watch registered (historyId=%s, expires=%s)",
            history_id,
            expiration,
        )
        return history_id
    except Exception as exc:
        logger.error("Failed to register Gmail watch: %s", exc)
        return None


def _fetch_new_messages(history_id: str) -> List[Dict[str, Any]]:
    """Fetch messages added since history_id via history.list."""
    try:
        from openjarvis.connectors.google_auth import call_with_refresh

        data = call_with_refresh(
            _api_history,
            str(_GMAIL_CREDS_PATH),
            history_id=history_id,
        )
        messages: List[Dict[str, Any]] = []
        for record in data.get("history", []):
            for msg_added in record.get("messagesAdded", []):
                messages.append(msg_added["message"])
        return messages
    except Exception as exc:
        logger.debug("history.list failed: %s", exc)
        return []


def _llm_triage(subject: str, sender: str, snippet: str) -> bool:
    """Ask a fast LLM whether this email warrants an alert. Returns True/False."""
    user_msg = f"From: {sender}\nSubject: {subject}\nPreview: {snippet[:300]}"
    try:
        client = _get_triage_client()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=3,
            temperature=0.0,
            system=[{"type": "text", "text": _TRIAGE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        answer = "".join(
            b.text for b in resp.content if hasattr(b, "text")
        ).strip().lower()
        important = answer.startswith("yes")
        logger.debug("LLM triage: %s → %s", subject[:50], "ALERT" if important else "skip")
        return important
    except Exception as exc:
        logger.warning("LLM triage failed, defaulting to skip: %s", exc)
        return False


def _llm_triage_batch(emails: List[Dict[str, str]]) -> List[bool]:
    """Triage a batch of emails in a single API call when possible.

    Args:
        emails: List of dicts with keys: subject, sender, snippet.

    Returns:
        List of bools, one per email, True meaning important.
    """
    if not emails:
        return []
    if len(emails) == 1:
        e = emails[0]
        return [_llm_triage(e["subject"], e["sender"], e["snippet"])]

    # Build a numbered list for the multi-email prompt.
    lines: List[str] = []
    for i, e in enumerate(emails, start=1):
        lines.append(
            f"{i}. From: {e['sender']}\n"
            f"   Subject: {e['subject']}\n"
            f"   Preview: {e['snippet'][:200]}"
        )
    user_msg = (
        "Evaluate each email below and reply with ONLY a numbered list, one per line, "
        "in the format '1 yes' or '1 no'. Example:\n"
        "1 yes\n2 no\n3 yes\n\nEmails:\n\n" + "\n\n".join(lines)
    )

    batch_system = (
        _TRIAGE_SYSTEM.rstrip()
        + "\n\nWhen given multiple emails, reply with a numbered list only: "
        "'<n> yes' or '<n> no' per line, one line per email. No other text."
    )

    try:
        client = _get_triage_client()
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=len(emails) * 10,
            temperature=0.0,
            system=[{"type": "text", "text": batch_system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        logger.debug("LLM batch triage raw response: %r", raw)

        # Parse "N yes/no" lines.
        result_map: Dict[int, bool] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit():
                idx = int(parts[0])
                answer = parts[1].lower()
                if 1 <= idx <= len(emails):
                    result_map[idx] = answer.startswith("yes")

        if len(result_map) == len(emails):
            results = [result_map[i] for i in range(1, len(emails) + 1)]
            for i, (e, r) in enumerate(zip(emails, results), start=1):
                logger.debug(
                    "LLM batch triage [%d/%d]: %s → %s",
                    i, len(emails), e["subject"][:50], "ALERT" if r else "skip",
                )
            return results

        logger.warning(
            "Batch triage parse failed (got %d/%d results), falling back to individual calls",
            len(result_map), len(emails),
        )
    except Exception as exc:
        logger.warning("LLM batch triage failed, falling back to individual calls: %s", exc)

    # Fallback: call individually.
    return [_llm_triage(e["subject"], e["sender"], e["snippet"]) for e in emails]


def _prefetch_and_prefilter(
    msg_id: str, important_senders: List[str]
) -> Optional[Dict[str, Any]]:
    """Fetch message metadata and run pre-filter + auto-pass logic without LLM.

    Returns:
        None  — email is filtered out (definite skip).
        dict with auto_pass=True  — email passes without LLM (starred / sender match).
        dict with auto_pass=False — email needs LLM triage; includes snippet.
    """
    try:
        from openjarvis.connectors.google_auth import call_with_refresh

        data = call_with_refresh(
            _api_get_message,
            str(_GMAIL_CREDS_PATH),
            msg_id=msg_id,
        )
        labels = set(data.get("labelIds", []))
        headers = {
            h["name"]: h["value"]
            for h in data.get("payload", {}).get("headers", [])
        }
        sender = headers.get("From", "")
        subject = headers.get("Subject", "(no subject)")
        snippet = data.get("snippet", "")
        has_unsubscribe = "List-Unsubscribe" in headers

        # --- Stage 1: Pre-filter obvious junk (no API call) ---
        if labels & _JUNK_LABELS:
            logger.debug("Pre-filter skip (junk label): %s", subject[:50])
            return None
        if _JUNK_SENDER_RE.search(sender):
            logger.debug("Pre-filter skip (junk sender): %s", sender[:50])
            return None
        if has_unsubscribe:
            passthrough_keywords = ("stripe", "quickbooks", "square",
                                    "calendly", "hubspot", "freshdesk",
                                    "3aces", "freightx")
            if not any(kw in sender.lower() for kw in passthrough_keywords):
                logger.debug("Pre-filter skip (unsubscribe header): %s", subject[:50])
                return None

        # --- Stage 2: Auto-pass (starred or explicit sender match) ---
        if "STARRED" in labels:
            return {"subject": subject, "sender": sender, "snippet": snippet, "auto_pass": True}
        if important_senders and any(
            s.lower() in sender.lower() for s in important_senders
        ):
            return {"subject": subject, "sender": sender, "snippet": snippet, "auto_pass": True}

        # Needs LLM triage.
        return {"subject": subject, "sender": sender, "snippet": snippet, "auto_pass": False}
    except Exception as exc:
        logger.debug("Message prefetch failed for %s: %s", msg_id, exc)
        return None


def _check_importance(
    msg_id: str, important_senders: List[str]
) -> Optional[Dict[str, str]]:
    """Three-stage importance check: pre-filter → auto-pass → LLM triage."""
    try:
        from openjarvis.connectors.google_auth import call_with_refresh

        data = call_with_refresh(
            _api_get_message,
            str(_GMAIL_CREDS_PATH),
            msg_id=msg_id,
        )
        labels = set(data.get("labelIds", []))
        headers = {
            h["name"]: h["value"]
            for h in data.get("payload", {}).get("headers", [])
        }
        sender = headers.get("From", "")
        subject = headers.get("Subject", "(no subject)")
        snippet = data.get("snippet", "")
        has_unsubscribe = "List-Unsubscribe" in headers

        # --- Stage 1: Pre-filter obvious junk (no API call) ---
        if labels & _JUNK_LABELS:
            logger.debug("Pre-filter skip (junk label): %s", subject[:50])
            return None
        if _JUNK_SENDER_RE.search(sender):
            logger.debug("Pre-filter skip (junk sender): %s", sender[:50])
            return None
        if has_unsubscribe:
            # Mailing lists — let LLM decide only if sender looks like
            # it could be a real business lead or client platform.
            passthrough_keywords = ("stripe", "quickbooks", "square",
                                    "calendly", "hubspot", "freshdesk",
                                    "3aces", "freightx")
            if not any(kw in sender.lower() for kw in passthrough_keywords):
                logger.debug("Pre-filter skip (unsubscribe header): %s", subject[:50])
                return None

        # --- Stage 2: Auto-pass (starred or explicit sender match) ---
        if "STARRED" in labels:
            return {"subject": subject, "sender": sender}
        if important_senders and any(
            s.lower() in sender.lower() for s in important_senders
        ):
            return {"subject": subject, "sender": sender}

        # --- Stage 3: LLM triage ---
        if _llm_triage(subject, sender, snippet):
            return {"subject": subject, "sender": sender}

        return None
    except Exception as exc:
        logger.debug("Message check failed for %s: %s", msg_id, exc)
        return None


def _send_email_alert(phone: str, subject: str, sender: str) -> None:
    """Send a Telegram alert for an important email."""
    try:
        from openjarvis.notifications import send_telegram

        clean_sender = sender.split("<")[0].strip().strip('"')
        message = f"Jarvis: Email\n\n{subject}\nFrom: {clean_sender}"
        send_telegram(message)
        logger.info("Email alert sent: %s", subject[:60])
    except Exception as exc:
        logger.error("Failed to send email alert: %s", exc)


def _on_pubsub_message(
    message: Any,
    *,
    phone: str,
    important_senders: List[str],
) -> None:
    """Pub/Sub message callback."""
    global _last_history_id

    try:
        raw = base64.b64decode(message.data).decode("utf-8")
        notification = json.loads(raw)
    except Exception:
        message.ack()
        return

    new_history_id = notification.get("historyId")
    if not new_history_id:
        message.ack()
        return

    start_id = _last_history_id or new_history_id
    new_messages = _fetch_new_messages(start_id)

    # Pass 1: fetch metadata and pre-filter (no LLM cost).
    # Collect candidates that survive the pre-filter but need LLM triage.
    auto_alerts: List[Dict[str, str]] = []
    triage_candidates: List[Dict[str, str]] = []

    for msg in new_messages:
        msg_id = msg.get("id", "")
        if not msg_id:
            continue
        pre = _prefetch_and_prefilter(msg_id, important_senders)
        if pre is None:
            continue
        if pre.get("auto_pass"):
            auto_alerts.append({"subject": pre["subject"], "sender": pre["sender"]})
        else:
            triage_candidates.append(pre)

    # Send alerts for auto-passed emails immediately.
    for alert in auto_alerts:
        _send_email_alert(phone, alert["subject"], alert["sender"])

    # Pass 2: batch LLM triage for the remaining candidates.
    if triage_candidates:
        batch_emails = [
            {"subject": c["subject"], "sender": c["sender"], "snippet": c["snippet"]}
            for c in triage_candidates
        ]
        decisions = _llm_triage_batch(batch_emails)
        for candidate, important in zip(triage_candidates, decisions):
            if important:
                _send_email_alert(phone, candidate["subject"], candidate["sender"])

    _last_history_id = new_history_id
    message.ack()


def start_gmail_listener(
    *,
    gcp_project: str,
    subscription: str,
    phone: str,
    important_senders: Optional[List[str]] = None,
    service_account_path: Optional[str] = None,
) -> bool:
    """Start the Pub/Sub streaming pull subscriber."""
    global _subscriber_future, _subscriber_client

    try:
        from functools import partial

        from google.cloud import pubsub_v1

        # Use service account key if provided, otherwise fall back to ADC
        if service_account_path:
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_file(
                service_account_path
            )
            _subscriber_client = pubsub_v1.SubscriberClient(
                credentials=credentials
            )
        else:
            _subscriber_client = pubsub_v1.SubscriberClient()
        subscription_path = _subscriber_client.subscription_path(
            gcp_project, subscription
        )

        callback = partial(
            _on_pubsub_message,
            phone=phone,
            important_senders=important_senders or [],
        )
        _subscriber_future = _subscriber_client.subscribe(
            subscription_path, callback=callback
        )
        logger.info(
            "Gmail Pub/Sub listener started on %s", subscription_path
        )
        return True
    except Exception as exc:
        logger.error("Failed to start Gmail listener: %s", exc)
        return False


def stop_gmail_listener() -> None:
    """Cancel the streaming pull and close the client."""
    global _subscriber_future, _subscriber_client

    if _subscriber_future is not None:
        _subscriber_future.cancel()
        try:
            _subscriber_future.result(timeout=5)
        except Exception:
            pass
        _subscriber_future = None

    if _subscriber_client is not None:
        _subscriber_client.close()
        _subscriber_client = None

    logger.info("Gmail Pub/Sub listener stopped")
