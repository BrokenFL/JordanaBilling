import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from jordana_invoice import software_updates as updates


def offer():
    commit='a'*40
    return dict(enabled=True,version='0.1.0.post37',release_label='v0.1.0-test.37',commit=commit,
                sha256='b'*64,url=updates.RELEASE_PREFIX+'v0.1.0-test.37/JordanaBilling-v0.1.0-test.37-'+commit[:12]+'-macos-arm64.dmg',notes='Fictional release')


class SoftwareUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.directory=Path(self.temp.name)/'updates'
        self.override=patch.object(updates,'update_dir',return_value=self.directory);self.override.start()
        updates._cache.clear()

    def tearDown(self):
        self.override.stop();self.temp.cleanup();updates._cache.clear()

    def test_only_newer_explicitly_promoted_release(self):
        self.assertIsNone(updates.validate_offer({'enabled':False},'0.1.0.post36'))
        self.assertIsNone(updates.validate_offer(offer(),'0.1.0.post37'))
        self.assertEqual(updates.validate_offer(offer(),'0.1.0.post9')['version'],'0.1.0.post37')
        for changed in [dict(url='https://example.invalid/payload.dmg'),dict(sha256='bad'),dict(commit='../'),dict(release_label='v0.1.0-test.38')]:
            with self.assertRaises(ValueError):updates.validate_offer({**offer(),**changed},'0.1.0.post36')

    def test_check_is_cached_and_offline_failure_nonblocking(self):
        with patch.object(updates,'fetch_offer',return_value=offer()) as fetch:
            self.assertTrue(updates.check_updates('0.1.0.post36')['available'])
            updates.check_updates('0.1.0.post36');self.assertEqual(fetch.call_count,1)
            updates.check_updates('0.1.0.post36',force=True);self.assertEqual(fetch.call_count,2)
        with patch.object(updates,'fetch_offer',side_effect=OSError('private path must not leak')):
            result=updates.check_updates('0.1.0.post36',force=True)
            self.assertTrue(result['unavailable']);self.assertNotIn('private path',str(result))

    def test_payload_manifest_rejects_tampering_and_traversal(self):
        payload=Path(self.temp.name)/'payload';(payload/'scripts').mkdir(parents=True)
        script=payload/'scripts/install_release.sh';script.write_text('fixture')
        manifest=dict(version='0.1.0.post37',release_label='v0.1.0-test.37',git_commit='a'*40,
                      source_tree_dirty=False,artifact={'contains_private_data':False},
                      checksums={'scripts/install_release.sh':hashlib.sha256(script.read_bytes()).hexdigest()})
        file=payload/'release_manifest.json';file.write_text(json.dumps(manifest))
        updates.verify_payload(payload,offer())
        script.write_text('changed')
        with self.assertRaises(ValueError):updates.verify_payload(payload,offer())
        manifest['checksums']={'../other':'a'*64};file.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):updates.verify_payload(payload,offer())

    def test_install_backs_up_before_detached_worker_and_blocks_duplicate(self):
        db=self.directory.parent/'data/jordana_invoice.sqlite3'
        events=[]
        with patch.object(updates.platform,'system',return_value='Darwin'),patch.object(updates.platform,'machine',return_value='arm64'),patch.object(updates,'fetch_offer',return_value=offer()),patch('jordana_invoice.backups.create_verified_backup',side_effect=lambda *a,**k:events.append('backup')),patch.object(updates.subprocess,'Popen',side_effect=lambda *a,**k:events.append('worker')):
            updates.start_update(db,'0.1.0.post36','0.1.0.post37')
            self.assertEqual(events,['backup','worker'])
            with self.assertRaisesRegex(ValueError,'already running'):updates.start_update(db,'0.1.0.post36','0.1.0.post37')

    def test_no_worker_when_backup_fails_or_release_changes(self):
        db=self.directory.parent/'data/jordana_invoice.sqlite3'
        with patch.object(updates.platform,'system',return_value='Darwin'),patch.object(updates.platform,'machine',return_value='arm64'),patch.object(updates,'fetch_offer',return_value=offer()),patch('jordana_invoice.backups.create_verified_backup',side_effect=RuntimeError('failed')),patch.object(updates.subprocess,'Popen') as spawn:
            with self.assertRaises(ValueError):updates.start_update(db,'0.1.0.post36','0.1.0.post38')
            with self.assertRaises(RuntimeError):updates.start_update(db,'0.1.0.post36','0.1.0.post37')
            spawn.assert_not_called()

    def test_failed_download_never_executes_installer(self):
        self.directory.mkdir();job=self.directory/'job';job.mkdir();(job/'offer.json').write_text(json.dumps(offer()))
        response=Mock();response.geturl.return_value='https://github.com/release';response.read.side_effect=[b'bad bytes',b'']
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with patch.object(updates,'urlopen',return_value=response),patch.object(updates.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'checksum'):updates.worker(job)
            run.assert_not_called()
            self.assertEqual(updates.install_status()['state'],'failed')

    def test_worker_verifies_before_install_and_always_unmounts(self):
        self.directory.mkdir();job=self.directory/'job';job.mkdir()
        data=offer();data['sha256']=hashlib.sha256(b'fixture dmg').hexdigest();(job/'offer.json').write_text(json.dumps(data))
        response=Mock();response.geturl.return_value='https://github.com/release';response.read.side_effect=[b'fixture dmg',b'']
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        commands=[]
        def run(command,**kwargs):
            commands.append(command)
            if command[0]=='bash':
                self.assertEqual(kwargs['env']['JORDANA_APP_SUPPORT_DIR'],str(self.directory.parent))
                self.assertIn('verified',commands)
        with patch.object(updates,'urlopen',return_value=response),patch.object(updates.subprocess,'run',side_effect=run),patch.object(updates,'verify_payload',side_effect=lambda *a:commands.append('verified')):
            updates.worker(job)
        self.assertEqual(commands[0][:2],['hdiutil','verify'])
        self.assertEqual(commands[-1][:2],['hdiutil','detach'])
        self.assertEqual(updates.install_status()['state'],'complete')

    def test_install_endpoint_uses_write_guard(self):
        import io
        from jordana_invoice.review_server import make_handler
        cls=make_handler(str(Path(self.temp.name)/'test.sqlite3'))
        def request(token):
            handler=object.__new__(cls);handler.path='/api/updates/install';body=b'{"version":"0.1.0.post37"}'
            handler.headers={'Host':'localhost','Content-Type':'application/json','Content-Length':str(len(body))}
            if token:handler.headers[cls.write_token_header]=cls.write_token
            handler.rfile=io.BytesIO(body);handler.wfile=io.BytesIO();captured={}
            handler.send_json=lambda payload,status=200:captured.update(payload=payload,status=status)
            handler.do_POST();return captured
        with patch.object(updates,'start_update',return_value={'ok':True}) as start:
            self.assertEqual(request(False)['status'],403);start.assert_not_called()
            self.assertEqual(request(True)['status'],200);start.assert_called_once()
