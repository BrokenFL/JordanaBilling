"""Fictional regressions for historical review; never load operational data."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jordana_invoice.db import connect, init_db
from jordana_invoice.importer import import_rows
from jordana_invoice.historical_review import reconcile_historical_review
from jordana_invoice.parser import parse_event
from jordana_invoice.review_services import (list_review_candidates, mark_candidate,
    restore_candidate, approve_candidate, create_person, save_person_alias)


def event(key, title='Alex Example 2', start='2026-08-10T14:00:00-04:00',
          end='2026-08-10T15:00:00-04:00', captured='2026-08-11T12:00:00-04:00',
          window='past_3_days', version='3', **extra):
    return dict(snapshot_key=key, run_id=key, batch_name=key, event_title=title,
                start_at=start, end_at=end, captured_at=captured, ingested_at=captured,
                capture_window=window, payload_version=version, calendar='Work',
                duration_minutes='60', **extra)


class HistoricalReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.conn=connect(Path(self.temp.name)/'test.sqlite3')
        init_db(self.conn)
        self.report_patch=patch('jordana_invoice.review_services.refresh_reports_after_commit')
        self.report_patch.start()

    def tearDown(self):
        self.report_patch.stop(); self.conn.close(); self.temp.cleanup()

    def load(self,*rows):
        import_rows(self.conn,list(rows),'fictional-test')

    def candidate(self,title='Alex Example 2'):
        return self.conn.execute('SELECT * FROM calendar_event_candidates WHERE title=?',(title,)).fetchone()

    def titles(self):
        return {r['raw_calendar_title'] for r in self.conn.execute("SELECT s.* FROM sessions s JOIN calendar_event_candidates c ON c.id=s.candidate_id WHERE s.review_status NOT IN ('approved','excluded') AND c.calendar_review_state='eligible'")}

    def test_old_future_only_never_enters_review_when_time_passes(self):
        self.load(event('old',captured='2026-08-09T12:00:00-04:00',window='next_7_days',version='2'))
        c=self.candidate();self.assertEqual(c['calendar_review_state'],'future_only')
        self.assertEqual(list_review_candidates(self.conn)['total'],0)
        with self.assertRaisesRegex(ValueError,'historical calendar'):
            approve_candidate(self.conn,c['id'],{})
        self.load(event('past'))
        self.assertEqual(self.titles(),{'Alex Example 2'})

    def test_v3_future_remains_raw_only(self):
        self.load(event('future',window='next_2_days'))
        self.assertIsNone(self.candidate())
        self.assertEqual(self.conn.execute('SELECT count(*) FROM raw_calendar_snapshots').fetchone()[0],1)

    def test_pre_end_past_observation_does_not_qualify(self):
        self.load(event('early',captured='2026-08-10T14:30:00-04:00'))
        self.assertIsNone(self.candidate())

    def test_late_cancellation_replaces_legacy_schedule(self):
        self.load(event('old',window='next_7_days',version='2',captured='2026-08-09T12:00:00-04:00'))
        self.load(event('new',title='Alex Example 2 late cx'))
        self.assertEqual(self.titles(),{'Alex Example 2 late cx'})
        self.assertEqual(self.candidate('Alex Example 2 late cx')['appointment_status'],'late_cancellation')

    def test_edited_duration_replaces_future_version_without_fuzzy_merge(self):
        self.load(event('old',window='next_7_days',version='2',captured='2026-08-09T12:00:00-04:00'))
        self.load(event('new',title='Alex Example 2 90'))
        self.assertEqual(self.titles(),{'Alex Example 2 90'})
        self.assertEqual(self.conn.execute("SELECT duration_minutes FROM sessions WHERE raw_calendar_title='Alex Example 2 90'").fetchone()[0],90)

    def test_absence_inside_observed_past_range_and_later_return(self):
        self.load(event('old'))
        self.load({**event('a',title='Morning Example 1',start='2026-08-10T13:00:00-04:00',end='2026-08-10T14:00:00-04:00',captured='2026-08-12T12:00:00-04:00'),'run_id':'new'},
                  {**event('b',title='Evening Example 4',start='2026-08-10T16:00:00-04:00',end='2026-08-10T17:00:00-04:00',captured='2026-08-12T12:00:00-04:00'),'run_id':'new'})
        self.assertEqual(self.candidate()['calendar_review_state'],'absent')
        self.load(event('return',captured='2026-08-13T12:00:00-04:00'))
        self.assertIn('Alex Example 2',self.titles())

    def test_boundary_truncation_does_not_erase_afternoon(self):
        self.load(event('old'))
        self.load(event('short',title='Early Example 10',start='2026-08-10T10:00:00-04:00',end='2026-08-10T11:00:00-04:00',captured='2026-08-12T12:00:00-04:00',window_start='2026-08-10T00:00:00-04:00',window_end='2026-08-10T23:59:59-04:00'))
        self.assertIn('Alex Example 2',self.titles())

    def test_aging_out_does_not_erase_historical_appointment(self):
        self.load(event('old'))
        self.load(event('later',title='Later Example 2',start='2026-08-20T14:00:00-04:00',end='2026-08-20T15:00:00-04:00',captured='2026-08-21T12:00:00-04:00'))
        self.assertIn('Alex Example 2',self.titles())

    def test_manual_exclusion_survives_sync_and_can_be_restored(self):
        self.load(event('first'));cid=self.candidate()['id']
        mark_candidate(self.conn,cid,classification='nonbillable')
        self.load(event('repeat',captured='2026-08-12T12:00:00-04:00'))
        self.assertEqual(self.candidate()['review_status'],'excluded')
        restore_candidate(self.conn,cid)
        reconcile_historical_review(self.conn)
        self.assertIn('Alex Example 2',self.titles())

    def test_upgrade_repairs_reopened_manual_exclusion_without_new_rows(self):
        self.load(event('first'));cid=self.candidate()['id']
        mark_candidate(self.conn,cid,classification='nonbillable')
        self.conn.execute("UPDATE sessions SET review_status='needs_person_match' WHERE candidate_id=?",(cid,))
        self.conn.execute("UPDATE calendar_event_candidates SET review_status='needs_person_match' WHERE id=?",(cid,))
        self.load()
        self.assertEqual(self.candidate()['review_status'],'excluded')
        self.assertEqual(self.conn.execute('SELECT review_status FROM sessions WHERE candidate_id=?',(cid,)).fetchone()[0],'excluded')

    def test_known_alias_links_existing_person_without_creating_people(self):
        person=create_person(self.conn,{'first_name':'Alex','last_name':'Example','display_name':'Alex Example'})
        save_person_alias(self.conn,person['person_id'],raw_alias='Lex',approved_by_user=True)
        self.load(event('alias',title='Lex 2'))
        self.assertEqual(self.conn.execute('SELECT person_id FROM session_participants').fetchone()[0],person['person_id'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM people').fetchone()[0],1)

    def test_approved_session_and_raw_evidence_unchanged_by_reconciliation(self):
        self.load(event('first'))
        self.conn.execute("UPDATE sessions SET review_status='approved',approved_rate_cents=23000")
        self.conn.execute("UPDATE calendar_event_candidates SET review_status='approved'")
        before=tuple(self.conn.execute('SELECT * FROM sessions').fetchone())
        raw=[tuple(r) for r in self.conn.execute('SELECT * FROM raw_calendar_snapshots')]
        reconcile_historical_review(self.conn)
        self.assertEqual(before,tuple(self.conn.execute('SELECT * FROM sessions').fetchone()))
        self.assertEqual(raw,[tuple(r) for r in self.conn.execute('SELECT * FROM raw_calendar_snapshots')])

    def test_reconciliation_is_idempotent(self):
        self.load(event('first'))
        counts=[self.conn.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['sessions','review_items','audit_log']]
        self.assertEqual(reconcile_historical_review(self.conn),0)
        self.assertEqual(counts,[self.conn.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['sessions','review_items','audit_log']])

    def test_shorthand_default_hour_and_explicit_half_hour(self):
        row={'start_at':'2026-08-10T14:00:00-04:00','end_at':'2026-08-10T14:30:00-04:00','duration_minutes':30}
        self.assertEqual(parse_event(dict(row,event_title='Alex Example 2')).proposed_duration_minutes,60)
        self.assertEqual(parse_event(dict(row,event_title='Alex Example 2 30')).proposed_duration_minutes,30)

    def test_recognizable_personal_entries_stay_out_of_billing_review(self):
        for i,title in enumerate(['Massage 4 90','Hair color 4','Reunion zoom 2']):
            # Seed legacy derived entries, then apply the upgraded classifier.
            with patch('jordana_invoice.parser.PERSONAL_KEYWORDS',set()):
                self.load(event(str(i),title=title))
        reconcile_historical_review(self.conn)
        self.assertEqual(list_review_candidates(self.conn)['total'],0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM raw_calendar_snapshots').fetchone()[0],3)

    def test_partial_capture_does_not_establish_absence(self):
        self.load(event('old'))
        self.conn.execute("INSERT INTO calendar_capture_runs (run_id,status,past_found,past_received,synced_at) VALUES ('partial','partial',3,2,'2026-08-12T12:00:00Z')")
        self.load({**event('a',title='Early Example 1',start='2026-08-10T13:00:00-04:00',end='2026-08-10T14:00:00-04:00',captured='2026-08-12T12:00:00-04:00'),'run_id':'partial'},
                  {**event('b',title='Later Example 4',start='2026-08-10T16:00:00-04:00',end='2026-08-10T17:00:00-04:00',captured='2026-08-12T12:00:00-04:00'),'run_id':'partial'})
        self.assertIn('Alex Example 2',self.titles())

    def test_complete_zero_event_normal_run_retires_missing_entry(self):
        self.load(event('old'))
        self.conn.execute("INSERT INTO calendar_capture_runs (run_id,batch_name,started_at,status,past_found,past_received,synced_at) VALUES ('empty','JORDANA_CALENDAR_empty','2026-08-12T12:00:00-04:00','complete',0,0,'2026-08-12T16:00:00Z')")
        self.load()
        self.assertEqual(self.candidate()['calendar_review_state'],'absent')

    def test_status_filter_cannot_surface_future_only_entry(self):
        self.load(event('old',window='next_7_days',version='2',captured='2026-08-09T12:00:00-04:00'))
        status=self.conn.execute('SELECT review_status FROM sessions').fetchone()[0]
        self.assertEqual(list_review_candidates(self.conn,review_status=status)['total'],0)

    def test_upgrade_refreshes_default_duration_but_preserves_saved_duration(self):
        self.load(event('old'))
        self.conn.execute('UPDATE sessions SET duration_minutes=30')
        self.load()
        self.assertEqual(self.conn.execute('SELECT duration_minutes FROM sessions').fetchone()[0],60)
        self.conn.execute('UPDATE sessions SET duration_minutes=90,approved_duration_minutes=90')
        self.load(event('later',captured='2026-08-12T12:00:00-04:00'))
        self.assertEqual(self.conn.execute('SELECT duration_minutes FROM sessions').fetchone()[0],90)
