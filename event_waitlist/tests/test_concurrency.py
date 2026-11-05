# Copyright 2026 SportJoe GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import threading
import time
from datetime import timedelta

import psycopg2

from odoo import SUPERUSER_ID, api, fields
from odoo.api import Transaction
from odoo.modules.registry import Registry
from odoo.tests import BaseCase, get_db_name, tagged


@tagged("post_install", "-at_install")
class TestEventWaitlistConcurrency(BaseCase):
    """Two cancellations committed at the same time must not offer the same
    person twice, nor give away more seats than are free. Uses real
    committed transactions in parallel threads."""

    def setUp(self):
        super().setUp()
        self.registry = Registry(get_db_name())
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            now = fields.Datetime.now()
            event = env["event.event"].create(
                {
                    "name": "Concurrency course",
                    "date_begin": now + timedelta(days=2),
                    "date_end": now + timedelta(days=2, hours=1),
                    "seats_limited": True,
                    "seats_max": 2,
                    "waitlist_enabled": True,
                    "event_ticket_ids": [(5, 0, 0)],
                    "event_mail_ids": [(5, 0, 0)],
                }
            )
            regs = env["event.registration"].create(
                [
                    {
                        "event_id": event.id,
                        "name": f"R{i}",
                        "email": f"r{i}@example.com",
                    }
                    for i in range(2)
                ]
            )
            entries = (
                env["event.waitlist"]
                .with_context(event_waitlist_no_mail=True)
                .create(
                    [
                        {"event_id": event.id, "name": n, "email": f"{n}@example.com"}
                        for n in ("anna", "ben", "cleo")
                    ]
                )
            )
            self.event_id = event.id
            self.reg_ids = regs.ids
            self.entry_ids = entries.ids

    def tearDown(self):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            event = env["event.event"].browse(self.event_id)
            event.waitlist_ids.unlink()
            event.registration_ids.unlink()
            event.unlink()
        super().tearDown()

    def _cancel_in_thread(self, reg_id, barrier, errors):
        """Cancel a registration in its own transaction, retrying on
        serialization failures like Odoo does for HTTP requests. The retry
        loop is written here because odoo.service.model.retrying() looks the
        registry up by name, which waits for the registry lock held by the
        test runner thread."""
        try:
            with self.registry.cursor() as cr:
                for attempt in range(5):
                    # Attach the registry ourselves (see docstring).
                    cr.transaction = Transaction(self.registry)
                    env = api.Environment(
                        cr, SUPERUSER_ID, {"event_waitlist_no_mail": True}
                    )
                    reg = env["event.registration"].browse(reg_id)
                    _snapshot = reg.state  # take the transaction snapshot
                    if attempt == 0:
                        barrier.wait(timeout=10)
                    try:
                        reg.action_cancel()
                        env.flush_all()
                        cr.commit()  # pylint: disable=invalid-commit
                        return
                    except psycopg2.errors.SerializationFailure:
                        cr.rollback()
                        time.sleep(0.2 * (attempt + 1))
                raise AssertionError("Too many serialization failures")
        except Exception as e:  # noqa: BLE001 pylint: disable=broad-except
            errors.append(e)

    def test_parallel_cancellations(self):
        barrier = threading.Barrier(2)
        errors = []
        threads = [
            threading.Thread(
                target=self._cancel_in_thread, args=(reg_id, barrier, errors)
            )
            for reg_id in self.reg_ids
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertFalse(errors, errors)
        with self.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            entries = env["event.waitlist"].browse(self.entry_ids)
            event = env["event.event"].browse(self.event_id)
            states = dict(
                zip(entries.mapped("name"), entries.mapped("state"), strict=True)
            )
            self.assertEqual(
                states, {"anna": "offered", "ben": "offered", "cleo": "waiting"}
            )
            self.assertEqual(event.waitlist_seats_held, 2)
            self.assertEqual(event.seats_available, 0)
