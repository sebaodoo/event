# Copyright 2026 SportJoe GmbH
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import re
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, tagged

from odoo.addons.event_waitlist.tests.common import WaitlistCommon


@tagged("post_install", "-at_install")
class TestWebsiteEventWaitlist(WaitlistCommon, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_waitlist_data(cls.env)
        cls.event.website_published = True

    def url_open(self, url, *args, **kwargs):
        self.env.flush_all()
        res = super().url_open(url, *args, **kwargs)
        self.env.invalidate_all()
        return res

    def _csrf(self, url=None):
        page = self.url_open("/web/login").text
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

    def _join(self, **data):
        url = f"/event/{self.event.id}/waitlist"
        values = {
            "csrf_token": self._csrf(url),
            "name": "Web Visitor",
            "email": "web.visitor@example.com",
            "seats": "1",
        }
        values.update(data)
        return self.url_open(url, data=values)

    def test_01_sold_out_page_shows_waitlist_button(self):
        html = self.url_open(f"/event/{self.event.id}/register").text
        self.assertIn("o_wevent_waitlist_join", html)
        self.event.waitlist_enabled = False
        html = self.url_open(f"/event/{self.event.id}/register").text
        self.assertNotIn("o_wevent_waitlist_join", html)

    def test_02_not_sold_out_no_button(self):
        self.reg_1.write({"state": "cancel"})
        html = self.url_open(f"/event/{self.event.id}/register").text
        self.assertNotIn("o_wevent_waitlist_join", html)

    def test_03_join_from_website(self):
        res = self._join(phone="+49 221 1")
        self.assertEqual(res.status_code, 200)
        self.assertIn("You are on the waiting list", res.text)
        entry = self.env["event.waitlist"].search([("event_id", "=", self.event.id)])
        self.assertEqual(len(entry), 1)
        self.assertEqual(
            (entry.name, entry.phone, entry.state),
            ("Web Visitor", "+49 221 1", "waiting"),
        )
        # Same email again: refused with a clear message
        res = self._join(email="WEB.visitor@example.com")
        self.assertIn("already on the waiting list", res.text)
        self.assertEqual(
            self.env["event.waitlist"].search_count([("event_id", "=", self.event.id)]),
            1,
        )

    def test_04_validation_and_honeypot(self):
        res = self._join(email="not-an-email")
        self.assertIn("valid email", res.text)
        res = self._join(seats="50")
        self.assertIn("between 1 and", res.text)
        self._join(website_url_hp="http://spam.example.com")
        self.assertFalse(
            self.env["event.waitlist"].search([("event_id", "=", self.event.id)])
        )

    def test_05_refused_when_not_sold_out_or_full(self):
        self.reg_1.write({"state": "cancel"})
        res = self._join()
        self.assertIn("not open", res.text)
        self.assertFalse(
            self.env["event.waitlist"].search([("event_id", "=", self.event.id)])
        )

    def test_10_token_page_and_accept(self):
        entry = self._add_entry("Alice")
        self.reg_1.write({"state": "cancel"})
        self.assertEqual(entry.state, "offered")
        url = f"/event/waitlist/{entry.access_token}"
        # GET never changes anything (mail scanners)
        html = self.url_open(url + "?action=accept").text
        self.assertIn("Yes, I take the seat", html)
        self.assertEqual(entry.state, "offered")
        res = self.url_open(url + "/accept", data={"csrf_token": self._csrf(url)})
        self.assertEqual(res.status_code, 200)
        entry.invalidate_recordset()
        self.assertEqual(entry.state, "confirmed")
        self.assertIn("your seat is confirmed", res.text)

    def test_11_token_leave_and_decline(self):
        alice = self._add_entry("Alice")
        bob = self._add_entry("Bob")
        url = f"/event/waitlist/{bob.access_token}"
        self.url_open(url + "/leave", data={"csrf_token": self._csrf(url)})
        bob.invalidate_recordset()
        self.assertEqual(bob.state, "cancelled")
        self.reg_1.write({"state": "cancel"})
        url = f"/event/waitlist/{alice.access_token}"
        self.url_open(url + "/decline", data={"csrf_token": self._csrf(url)})
        alice.invalidate_recordset()
        self.assertEqual(alice.state, "declined")

    def test_12_bad_tokens(self):
        self.assertEqual(self.url_open("/event/waitlist/short").status_code, 404)
        self.assertEqual(self.url_open("/event/waitlist/" + "x" * 43).status_code, 404)
        entry = self._add_entry("Alice")
        res = self.url_open(f"/event/waitlist/{entry.access_token}/accept")
        self.assertNotEqual(res.status_code, 200)  # GET not allowed

    def test_13_expired_offer_on_page(self):
        alice = self._add_entry("Alice")
        bob = self._add_entry("Bob")
        self.reg_1.write({"state": "cancel"})
        alice.offer_deadline = fields.Datetime.now() - timedelta(minutes=1)
        html = self.url_open(f"/event/waitlist/{alice.access_token}").text
        self.assertIn("was not confirmed in time", html)
        bob.invalidate_recordset()
        self.assertEqual(bob.state, "offered")

    def test_20_slots(self):
        day = (fields.Datetime.now() + timedelta(days=5)).date()
        event = self.env["event.event"].create(
            {
                "name": "Weekly yoga",
                "date_begin": fields.Datetime.now() + timedelta(days=4),
                "date_end": fields.Datetime.now() + timedelta(days=14),
                "seats_limited": True,
                "seats_max": 1,
                "waitlist_enabled": True,
                "website_published": True,
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
        slot_1, slot_2 = event.event_slot_ids.sorted("date")
        self.env["event.registration"].create(
            {"event_id": event.id, "event_slot_id": slot_1.id, "name": "Full"}
        )
        html = self.url_open(f"/event/{event.id}/register").text
        self.assertIn("o_wevent_waitlist_join", html)
        html = self.url_open(f"/event/{event.id}/waitlist").text
        slot_select = html.split('name="slot_id"', 1)[1].split("</select>", 1)[0]
        self.assertIn(f'value="{slot_1.id}"', slot_select)
        self.assertNotIn(f'value="{slot_2.id}"', slot_select)
        url = f"/event/{event.id}/waitlist"
        data = {
            "csrf_token": self._csrf(),
            "name": "Slot Visitor",
            "email": "slot.visitor@example.com",
            "seats": "1",
        }
        res = self.url_open(url, data=dict(data, slot_id=str(slot_2.id)))
        self.assertIn("not open", res.text)
        res = self.url_open(url, data=dict(data, slot_id=str(slot_1.id)))
        self.assertIn("You are on the waiting list", res.text)
        entry = self.env["event.waitlist"].search([("event_id", "=", event.id)])
        self.assertEqual(entry.event_slot_id, slot_1)
