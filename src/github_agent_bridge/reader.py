from __future__ import annotations

import email
import imaplib
from dataclasses import dataclass

from .models import Notification
from .parser import decode_header_value, extract_body_text, is_github_notification_message, parse_auth_results
from .policy import Policy
from .queue import JobQueue


def imap_mailbox_arg(value: str) -> str:
    """Quote mailbox names with spaces for imaplib.select."""
    if " " in value and not value.startswith('"'):
        return f'"{value}"'
    return value


@dataclass(frozen=True)
class ImapConfig:
    host: str
    port: int
    username: str
    password: str
    mailbox: str = "INBOX"


class ImapReader:
    """Fast IMAP reader: fetch -> enqueue durable job -> advance high-water.

    It intentionally never dispatches OpenClaw agents. Slow work belongs to ExecutorPool.
    """

    def __init__(self, config: ImapConfig, queue: JobQueue, policy: Policy, mark_seen: bool = False):
        self.config = config
        self.queue = queue
        self.policy = policy
        self.mark_seen = mark_seen

    def fetch_once(self) -> int:
        for attempt in range(2):
            try:
                return self._fetch_once()
            except imaplib.IMAP4.abort:
                if attempt:
                    raise
        return 0

    def _fetch_once(self) -> int:
        last_uid = int(self.queue.get_state("last_uid", "0") or 0)
        count = 0
        imap = imaplib.IMAP4_SSL(self.config.host, self.config.port)
        try:
            imap.login(self.config.username, self.config.password)
            imap.select(imap_mailbox_arg(self.config.mailbox))
            status, data = imap.uid("search", None, f"UID {last_uid + 1}:*")
            if status != "OK" or not data or not data[0]:
                return 0
            uids = sorted(int(x) for x in data[0].split() if int(x) > last_uid)
            for uid in uids:
                st, msgd = imap.uid("fetch", str(uid), "(RFC822)")
                if st != "OK" or not msgd or not msgd[0]:
                    break
                msg = email.message_from_bytes(msgd[0][1])
                from_addr = decode_header_value(msg.get("From", ""))
                subject = decode_header_value(msg.get("Subject", ""))
                message_id = decode_header_value(msg.get("Message-ID", ""))
                if is_github_notification_message(msg, from_addr):
                    n = Notification(uid=uid, message_id=message_id, subject=subject, from_addr=from_addr, body=extract_body_text(msg), auth=parse_auth_results(msg))
                    self.queue.enqueue(n, self.policy)
                    # Only GitHub notifications belong to this bounded context.
                    # Generic/non-GitHub mail must remain untouched for the generic inbox worker.
                    if self.mark_seen:
                        imap.uid("store", str(uid), "+FLAGS", "(\\Seen)")
                    count += 1
                self.queue.set_state("last_uid", str(uid))
            return count
        finally:
            try:
                imap.logout()
            except Exception:
                pass
