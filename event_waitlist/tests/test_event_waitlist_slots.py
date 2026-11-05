# Copyright 2026 SportJoe GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

from .common import WaitlistCommon


@tagged("post_install", "-at_install")
class TestEventWaitlistSlots(WaitlistCommon, TransactionCase):
    """Events with several time slots (Odoo 19+): the waiting list is per slot."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_waitlist_data(cls.env)
        day = (fields.Datetime.now() + timedelta(days=5)).date()
        cls.slot_event = cls.env["event.event"].create(
            {
                "name": "Weekly yoga",
                "date_begin": fields.Datetime.now() + timedelta(days=4),
                "date_end": fields.Datetime.now() + timedelta(days=14),
                "seats_limited": True,
                "seats_max": 1,
                "waitlist_enabled": True,
                "is_multi_slots": True,
                "event_ticket_ids": [(5, 0, 0)],
                "event_mail_ids": [(5, 0, 0)],
                "event_slot_ids": [
                    (0, 0, {"date": day, "start_hour": 18.0, "end_hour": 19.0}),
                    (
                        0,
                        0,
                        {
                            "date": day + timedelta(days=7),
                            "start_hour": 18.0,
                            "end_hour": 19.0,
                        },
                    ),
                ],
            }
        )
        cls.slot_1, cls.slot_2 = cls.slot_event.event_slot_ids.sorted("date")
        cls.reg_s1 = cls.env["event.registration"].create(
            {
                "event_id": cls.slot_event.id,
                "event_slot_id": cls.slot_1.id,
                "name": "Slot 1 attendee",
            }
        )

    def test_01_joinable_only_for_sold_out_slot(self):
        self.assertTrue(self.slot_1.is_sold_out)
        self.assertFalse(self.slot_2.is_sold_out)
        self.assertEqual(self.slot_event._waitlist_joinable_slots(), self.slot_1)
        self.assertTrue(self.slot_event._waitlist_can_join(slot=self.slot_1))
        self.assertFalse(self.slot_event._waitlist_can_join(slot=self.slot_2))

    def test_02_slot_required(self):
        with self.assertRaises(ValidationError):
            self._add_entry("No slot", event=self.slot_event)

    def test_03_offer_on_cancellation_in_same_slot(self):
        alice = self._add_entry(
            "Alice", event=self.slot_event, event_slot_id=self.slot_1.id
        )
        self.assertEqual(alice.state, "waiting")
        # A free seat in another slot does not concern Alice
        self.env["event.registration"].create(
            {
                "event_id": self.slot_event.id,
                "event_slot_id": self.slot_2.id,
                "name": "Slot 2",
            }
        ).action_cancel()
        self.assertEqual(alice.state, "waiting")
        self.reg_s1.action_cancel()
        self.assertEqual(alice.state, "offered")
        self.assertLessEqual(alice.offer_deadline, self.slot_1.start_datetime)
        # The seat of slot 1 is held: nobody else can book it
        self.assertEqual(self.slot_1.seats_available, 0)
        self.assertEqual(self.slot_1.waitlist_seats_held, 1)
        with self.assertRaises(ValidationError):
            self.env["event.registration"].create(
                {
                    "event_id": self.slot_event.id,
                    "event_slot_id": self.slot_1.id,
                    "name": "Intruder",
                }
            )
        alice.action_accept_offer()
        self.assertEqual(alice.registration_ids.event_slot_id, self.slot_1)
        self.assertEqual(alice.registration_ids.state, "open")

    def test_04_same_email_different_slots(self):
        self.env["event.registration"].create(
            {
                "event_id": self.slot_event.id,
                "event_slot_id": self.slot_2.id,
                "name": "Slot 2",
            }
        )
        self._add_entry(
            "Alice",
            event=self.slot_event,
            event_slot_id=self.slot_1.id,
            email="alice@example.com",
        )
        self._add_entry(
            "Alice",
            event=self.slot_event,
            event_slot_id=self.slot_2.id,
            email="alice@example.com",
        )
        with self.assertRaises(ValidationError):
            self._add_entry(
                "Alice",
                event=self.slot_event,
                event_slot_id=self.slot_1.id,
                email="ALICE@example.com",
            )

    def test_05_no_offer_after_slot_start(self):
        alice = self._add_entry(
            "Alice", event=self.slot_event, event_slot_id=self.slot_1.id
        )
        self.slot_event.date_begin = fields.Datetime.now() - timedelta(days=3)
        self.slot_1.write(
            {"date": fields.Date.today() - timedelta(days=1), "start_hour": 8.0}
        )
        self.reg_s1.action_cancel()
        self.assertEqual(alice.state, "waiting")

    def test_06_slot_and_ticket(self):
        event = self.env["event.event"].create(
            {
                "name": "Slots and tickets",
                "date_begin": fields.Datetime.now() + timedelta(days=2),
                "date_end": fields.Datetime.now() + timedelta(days=5),
                "is_multi_slots": True,
                "waitlist_enabled": True,
                "event_mail_ids": [(5, 0, 0)],
                "event_slot_ids": [
                    (
                        0,
                        0,
                        {
                            "date": (fields.Datetime.now() + timedelta(days=3)).date(),
                            "start_hour": 10.0,
                            "end_hour": 11.0,
                        },
                    )
                ],
                "event_ticket_ids": [
                    (5, 0, 0),
                    (0, 0, {"name": "Members", "seats_max": 1}),
                    (0, 0, {"name": "Guests", "seats_max": 5}),
                ],
            }
        )
        slot = event.event_slot_ids
        members, guests = event.event_ticket_ids
        reg = self.env["event.registration"].create(
            {
                "event_id": event.id,
                "event_slot_id": slot.id,
                "event_ticket_id": members.id,
                "name": "M1",
            }
        )
        self.assertTrue(event._waitlist_can_join(members, slot))
        self.assertFalse(event._waitlist_can_join(guests, slot))
        alice = self._add_entry(
            "Alice", event=event, event_slot_id=slot.id, event_ticket_id=members.id
        )
        reg.action_cancel()
        self.assertEqual(alice.state, "offered")
        self.assertEqual(event._get_seats_availability([(slot, members)]), [0])
        self.assertEqual(event._get_seats_availability([(slot, guests)]), [5])
        alice.action_accept_offer()
        self.assertEqual(alice.registration_ids.event_ticket_id, members)
        self.assertEqual(event._get_seats_availability([(slot, members)]), [0])
