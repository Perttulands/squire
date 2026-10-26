import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

loader = importlib.machinery.SourceFileLoader('squire', str(Path(__file__).resolve().parents[1] / 'squire'))
spec = importlib.util.spec_from_loader(loader.name, loader)
squire = importlib.util.module_from_spec(spec)
loader.exec_module(squire)


def record(text='work', timestamp=1000):
    return json.dumps({'type': 'response_item', 'timestamp': timestamp, 'payload': {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}}) + '\n'


def titled_claude_frame(draft=''):
    # Real %431 chrome at cursor_y=14; neutral scrollback preserves tool decoys.
    scrollback = ['Earlier neutral response.', '     ────', '     ❯'] + [''] * 10
    return '\n'.join(scrollback + [
        '─' * 51 + ' Athena ─', '❯\xa0' + draft, '─' * 60,
        '  Opus 5.5 · medium · ctx 14% · 5h 17%',
        '  ⏵⏵ bypass permissions on · 1 shell · /tasks to see subagents · ← for agents',
        '✔ Update installed · Restart to update'.rjust(100), '/rc'.rjust(100), '',
        '  ● main', '  ◯ opus-medium Removing skilltest dir, verifying … 22m 52s · ↓ 134.9k tokens', '',
    ])


def queued_attachment(prompt='Please check the documented requirement.', origin='human', mode='prompt'):
    # Native Claude queued-command shape, with synthetic human text.
    return {'type': 'attachment', 'attachment': {
        'type': 'queued_command', 'prompt': prompt, 'commandMode': mode,
        'origin': {'kind': origin}, 'humanTurn': origin == 'human',
        'timestamp': '2026-10-17T09:44:00Z',
    }}


class Mechanics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.directory = self.home / 'squire-test'
        self.directory.mkdir()
        self.source = self.home / 'native.jsonl'
        self.source.write_text(record())
        self.config = {'socket': '/sock', 'pane': '%1', 'session': '$1', 'pane_pid': '101', 'harness': 'codex', 'transcript': str(self.source), 'stopped': False, 'interval': 360, 'model': 'gpt-6.1-sol', 'effort': 'medium'}
        squire.save(self.directory / 'config.json', self.config)
        squire.save(self.directory / 'notes.json', {'plan': 'Check the upstream anchor.', 'trajectory': '', 'said': [], 'tuning': []})
        squire.save(self.directory / 'cursor.json', {'offset': 0, 'line': 0, 'identity': None})
        self.decision = {'message': '', 'trajectory': 'The upstream anchor still needs checking.', 'withdraw_undelivered': False}
        self.screen = ''
        self.copy_mode = False
        self.client = ''
        self.sent = []

    def transport(self, socket, verb, *args):
        if verb == 'display-message':
            return '%1\t$1\t101\tcodex\t' + str(int(self.copy_mode)) + '\t0'
        if verb == 'capture-pane':
            return '› ' + self.screen + '\n\nGPT-6.1-Sol medium'
        if verb == 'list-clients':
            return self.client
        if verb == 'send-keys':
            self.sent.append(args)
            if '-l' in args:
                self.screen += args[-1]
            elif args[-1] == 'C-u':
                self.screen = ''
            elif args[-1] == 'Enter':
                self.screen = ''
            return ''
        raise AssertionError(verb)

    def judge_context(self, side_effect=None, decision=None):
        return patch.object(squire, 'judge', side_effect=side_effect, return_value=decision or self.decision)

    def test_target_uses_exact_absolute_socket_and_pane(self):
        with patch.object(squire, 'tmux', return_value='%12\t$3\t@4\t655\t/work') as call:
            actual = squire.detect_target({'TMUX': '/tmp/a,b,31,2', 'TMUX_PANE': '%12'})
        self.assertEqual(actual['socket'], '/tmp/a,b')
        self.assertEqual(call.call_args.args[0], '/tmp/a,b')
        for env in ({}, {'TMUX': 'relative,31,2', 'TMUX_PANE': '%2'}):
            with self.assertRaises(ValueError):
                squire.detect_target(env)

    def test_discovery_selects_thread_and_refuses_ambiguity(self):
        folder = self.home / '.codex/sessions/2026'
        folder.mkdir(parents=True)
        first = folder / 'rollout-time-thread1.jsonl'
        first.touch()
        self.assertEqual(squire.discover_transcript('codex', '/work', {'CODEX_THREAD_ID': 'thread1'}, self.home)[1], first)
        (folder / 'other-thread1.jsonl').touch()
        with self.assertRaisesRegex(ValueError, '2 candidates'):
            squire.discover_transcript('codex', '/work', {'CODEX_THREAD_ID': 'thread1'}, self.home)
        claude = self.home / '.claude/projects/-srv-my-project'
        claude.mkdir(parents=True)
        (claude / 'one.jsonl').touch()
        (claude / 'two.jsonl').touch()
        with self.assertRaises(ValueError):
            squire.discover_transcript('claude', '/srv/my_project', {}, self.home)
        self.assertEqual(squire.discover_transcript('claude', '/srv/my_project', {'CLAUDE_SESSION_ID': 'one'}, self.home)[1].stem, 'one')

    def test_codex_discovery_uses_custom_home(self):
        codex_home = self.home / 'custom codex'
        folder = codex_home / 'sessions/2026'
        folder.mkdir(parents=True)
        transcript = folder / 'rollout-thread1.jsonl'
        transcript.touch()
        env = {'CODEX_HOME': str(codex_home), 'CODEX_THREAD_ID': 'thread1'}
        self.assertEqual(squire.discover_transcript('codex', '/work', env, self.home)[1], transcript)

    def test_allowlist_rejects_unknown_malformed_and_other_harness_records(self):
        ignored = [{'type': kind} for kind in ('world_state', 'token_usage_record', 'compacted', 'attachment', 'system', 'last-prompt', 'api-request-start')]
        ignored += [None, [], {'type': 'response_item', 'payload': None}, {'type': 'event_msg', 'payload': None}]
        ignored += [{'type': 'response_item', 'payload': {'type': 'reasoning', 'summary': []}}]
        ignored += [queued_attachment(origin='task-notification', mode='task-notification'),
                    {'type': 'attachment', 'attachment': {'type': 'total_tokens_reminder', 'origin': {'kind': 'human'}, 'prompt': 'Bookkeeping'}},
                    {'type': 'attachment', 'attachment': None},
                    {'type': 'attachment', 'attachment': {'type': 'queued_command', 'origin': None, 'prompt': 'Malformed'}},
                    queued_attachment(prompt=None), queued_attachment(prompt={'text': 'Malformed'}),
                    queued_attachment(origin='channel', mode='task-notification'),
                    queued_attachment(origin='channel', mode=None)]
        ignored += [queued_attachment(prompt=prompt, origin=origin, mode=mode)
                    for origin in ('peer', 'coordinator', 'task-notification')
                    for mode in ('prompt', 'task-notification')
                    for prompt in ('Automated message', [{'type': 'text', 'text': 'Automated message'}])]
        for value in ignored:
            for harness in (None, 'claude', 'codex'):
                with self.subTest(value=value, harness=harness):
                    self.assertEqual(squire.render_record(json.dumps(value) + '\n', harness)[1], '')
        self.assertEqual(squire.render_record('invalid json\n')[1], '')
        self.assertEqual(squire.render_record(json.dumps({'type': 'assistant', 'message': {'role': 'assistant', 'content': 'hello'}}), 'codex')[1], '')
        self.assertEqual(squire.render_record(record(), 'claude')[1], '')
        self.assertTrue(squire.render_record(record(), 'codex')[1])
        claude = {'type': 'assistant', 'message': {'role': 'assistant', 'content': [{'type': 'text', 'text': 'check anchor'}]}}
        self.assertIn('check anchor', squire.render_record(json.dumps(claude), 'claude')[1])

    def test_queued_human_message_is_rendered_as_user_and_triggers_a_check(self):
        event = queued_attachment()
        raw = json.dumps(event) + '\n'
        for harness in (None, 'claude'):
            clean, rendered = squire.render_record(raw, harness)
            user = json.loads(rendered)
            self.assertEqual(user['type'], 'user')
            self.assertTrue(user['queued_mid_turn'])
            self.assertEqual(user['timestamp'], event['attachment']['timestamp'])
            self.assertEqual(user['message'], {'role': 'user', 'content': event['attachment']['prompt']})
            self.assertEqual(json.loads(clean), user)
        self.assertEqual(squire.render_record(raw, 'codex')[1], '')
        self.source.write_text(raw)
        squire.save(self.directory / 'config.json', dict(self.config, harness='claude'))
        with patch.object(squire, 'validate_target'), self.judge_context() as judge, patch('builtins.print'):
            self.assertTrue(squire.check_once(self.directory))
            self.assertFalse(squire.check_once(self.directory))
        self.assertEqual(judge.call_count, 1)
        self.assertIn(event['attachment']['prompt'], judge.call_args.kwargs['rendered'])
        self.assertEqual(squire.load(self.directory / 'cursor.json')['offset'], len(raw.encode()))

    def test_channel_prompt_is_rendered_as_a_human_message(self):
        event = queued_attachment(origin='channel')
        self.assertFalse(event['attachment']['humanTurn'])
        for harness in (None, 'claude'):
            clean, rendered = squire.render_record(json.dumps(event), harness)
            user = json.loads(rendered)
            self.assertEqual(user['type'], 'user')
            self.assertTrue(user['queued_mid_turn'])
            self.assertEqual(user['message'], {'role': 'user', 'content': event['attachment']['prompt']})
            self.assertEqual(json.loads(clean), user)
        self.assertEqual(squire.render_record(json.dumps(event), 'codex')[1], '')

    def test_queued_human_and_channel_list_prompts_keep_text_and_strip_images(self):
        records = []
        for origin in ('human', 'channel'):
            prompt = [
                {'type': 'text', 'text': f'Compare the {origin} image at /tmp/reference.png.'},
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'QUEUED_PIXELS' * 100}},
            ]
            event = queued_attachment(prompt=prompt, origin=origin)
            raw = json.dumps(event) + '\n'
            records.append(raw)
            for harness in (None, 'claude'):
                with self.subTest(origin=origin, harness=harness):
                    clean, rendered = squire.render_record(raw, harness)
                    user = json.loads(rendered)
                    self.assertEqual(user['type'], 'user')
                    self.assertTrue(user['queued_mid_turn'])
                    self.assertEqual(user['message']['role'], 'user')
                    self.assertEqual(user['message']['content'][0], prompt[0])
                    self.assertEqual(user['message']['content'][1]['source']['data'], squire.MEDIA_MARKER)
                    self.assertEqual(json.loads(clean), user)
                    self.assertNotIn('QUEUED_PIXELS', clean + rendered)
            self.assertEqual(squire.render_record(raw, 'codex')[1], '')
        self.source.write_text(''.join(records))
        squire.save(self.directory / 'config.json', dict(self.config, harness='claude'))
        with patch.object(squire, 'validate_target'), self.judge_context() as judge, patch('builtins.print'):
            self.assertTrue(squire.check_once(self.directory))
            self.assertFalse(squire.check_once(self.directory))
        judge.assert_called_once()
        rendered = judge.call_args.kwargs['rendered']
        self.assertIn('Compare the human image', rendered)
        self.assertIn('Compare the channel image', rendered)
        self.assertNotIn('QUEUED_PIXELS', rendered)
        self.assertEqual(squire.load(self.directory / 'cursor.json')['offset'], self.source.stat().st_size)

    def test_native_bookkeeping_tail_advances_without_model(self):
        self.source.write_text(''.join(json.dumps({'type': kind}) + '\n' for kind in ('world_state', 'token_usage_record', 'compacted')))
        with patch.object(squire, 'validate_target'), self.judge_context() as judge:
            self.assertTrue(squire.check_once(self.directory))
            self.assertFalse(squire.check_once(self.directory))
            judge.assert_not_called()
        self.assertEqual(squire.load(self.directory / 'cursor.json')['offset'], self.source.stat().st_size)

    def test_media_cipher_and_compaction_do_not_consume_judgment_budget(self):
        image = {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': '/9j/' + 'A' * 4400000}}
        lines = [record('Earlier anchor is wrong.'), json.dumps({'type': 'user', 'message': {'role': 'user', 'content': [image]}, 'toolUseResult': {'content': [image], 'path': '/tmp/hand.jpg'}}) + '\n', json.dumps({'type': 'compacted', 'payload': {'history': 'OLD' * 200000}}) + '\n', json.dumps({'type': 'response_item', 'payload': {'type': 'reasoning', 'summary': [{'text': 'Check original anchor.'}], 'encrypted_content': 'CIPHER' * 200000}}) + '\n', record('Later animation has the symptom.')]
        self.source.write_text(''.join(lines))
        cursor = {'offset': 0, 'line': 0, 'identity': None}
        prepared = list(squire.live_batches(self.source, cursor))
        self.assertEqual(len(prepared), 1)
        batch = prepared[0]
        self.assertEqual(batch['cursor']['offset'], self.source.stat().st_size)
        self.assertEqual(batch['last'], 5)
        for opaque in ('/9j/', 'A' * 200, 'CIPHER', 'OLD'):
            self.assertNotIn(opaque, batch['rendered'])
        self.assertIn('/tmp/hand.jpg', batch['rendered'])
        self.assertIn('Check original anchor.', batch['rendered'])
        self.assertIn('Later animation', batch['rendered'])

    def test_shared_batches_render_once_and_preserve_exact_ranges(self):
        lines = [record('x' * 9000, 1000 + n) for n in range(40)]
        self.source.write_text(''.join(lines))
        initial = {'offset': 0, 'line': 0, 'identity': None}
        original = squire.render_record
        with patch.object(squire, 'render_record', wraps=original) as render:
            live = list(squire.live_batches(self.source, initial, harness='codex'))
        self.assertEqual(render.call_count, len(lines))
        offline = list(squire.batches((line.encode() for line in lines), initial, 'codex'))
        self.assertEqual([(b['first'], b['last'], b['rendered']) for b in live], [(b['first'], b['last'], b['rendered']) for b in offline])
        self.assertEqual([line for batch in live for line in range(batch['first'], batch['last'] + 1)], list(range(1, 41)))
        self.assertTrue(all(len(batch['rendered']) <= squire.INPUT_BUDGET for batch in live))
        self.assertEqual(live[-1]['cursor']['offset'], self.source.stat().st_size)

    def test_partial_tail_future_boundary_and_replacement(self):
        complete = record('now')
        self.source.write_text(complete + '{"unfinished":')
        through = self.source.stat().st_size
        with self.source.open('a') as handle:
            handle.write('true}\n' + record('future'))
        cursor = {'offset': 0, 'line': 0, 'identity': None}
        first = list(squire.live_batches(self.source, cursor, through))[0]
        self.assertEqual(first['cursor']['offset'], len(complete.encode()))
        self.assertNotIn('future', first['rendered'])
        self.source.rename(self.home / 'old-native.jsonl')
        self.source.write_text(record('replacement'))
        with self.assertRaisesRegex(ValueError, 'replaced|truncated'):
            list(squire.live_batches(self.source, first['cursor']))

    def test_paid_judgment_commits_before_deferred_delivery_without_rejudge(self):
        observed = []
        def blocked(directory, message, expected_tuning=None):
            observed.append((squire.load(directory / 'cursor.json')['line'], squire.load(directory / 'notes.json')['trajectory']))
            raise squire.PaneBlocked('existing human draft')
        decision = dict(self.decision, message='Check upstream.')
        with patch.object(squire, 'validate_target'), self.judge_context(decision=decision) as judge, patch.object(squire, 'deliver', side_effect=blocked), patch('builtins.print'):
            self.assertTrue(squire.check_once(self.directory))
            self.assertFalse(squire.check_once(self.directory))
        self.assertEqual(judge.call_count, 1)
        self.assertEqual(observed, [(1, decision['trajectory'])])
        pending = squire.load(self.directory / 'pending.json')
        self.assertAlmostEqual(pending['retry_after'] - squire.time.time(), 360, delta=2)
        pending['retry_after'] = 0
        squire.save(self.directory / 'pending.json', pending)
        with self.judge_context() as judge, patch.object(squire, 'deliver', return_value=True):
            self.assertTrue(squire.deliver_pending(self.directory))
            judge.assert_not_called()
        self.assertFalse((self.directory / 'pending.json').exists())

    def test_silent_checks_keep_deferred_advice_until_a_new_valid_nudge(self):
        pending = {'message': 'Check upstream.', 'tuning_version': 0, 'since': 1000,
                   'deferred_reason': 'existing human draft', 'deferral_count': 2,
                   'retry_after': squire.time.time() + 360, 'held': False}
        squire.save(self.directory / 'pending.json', pending)
        with patch.object(squire, 'validate_target'), self.judge_context(), patch.object(squire, 'deliver') as deliver, patch('builtins.print'):
            self.assertTrue(squire.check_once(self.directory))
            deliver.assert_not_called()
        self.assertEqual(squire.load(self.directory / 'pending.json'), pending)
        self.assertEqual(squire.load(self.directory / 'cursor.json')['line'], 1)
        self.assertEqual(squire.load(self.directory / 'notes.json')['trajectory'], self.decision['trajectory'])
        notes = squire.load(self.directory / 'notes.json')
        with patch('builtins.print'):
            for message in ('\x7fInvalid advice', '   '):
                squire.commit_decision(self.directory, notes, dict(self.decision, message=message), tuning_version=0)
                self.assertEqual(squire.load(self.directory / 'pending.json'), pending)
        with patch.object(squire.time, 'time', return_value=2000):
            squire.commit_decision(self.directory, notes, dict(self.decision, message='New evidence upstream.'), tuning_version=0)
        self.assertEqual(squire.load(self.directory / 'pending.json'), {
            'message': 'New evidence upstream.', 'tuning_version': 0, 'since': 2000,
            'held': False, 'deferred_reason': 'existing human draft', 'deferral_count': 2})

    def test_catch_up_queues_only_the_latest_nudge_and_delivers_after_final_silence(self):
        self.source.write_text(record('first') + record('second') + record('third'))
        observed = []
        def judge(directory, config, notes, data, first, last, **kwargs):
            path = directory / 'pending.json'
            observed.append(squire.load(path)['message'] if path.exists() else None)
            return dict(self.decision, message={1: 'First nudge.', 2: 'Newest nudge.', 3: ''}[first])
        def deliver(directory, message, expected_tuning):
            self.assertEqual(squire.load(directory / 'cursor.json')['line'], 3)
            self.assertEqual(message, 'Newest nudge.')
            return True
        class Finished(Exception):
            pass
        with patch.object(squire, 'INPUT_BUDGET', 1), patch.object(squire, 'pair_dir', return_value=self.directory), patch.object(squire, 'validate_target'), self.judge_context(side_effect=judge) as judged, patch.object(squire, 'deliver', side_effect=deliver) as delivered, patch.object(squire, 'wait_for_work', side_effect=Finished), patch('builtins.print'):
            with self.assertRaises(Finished):
                squire.worker('squire-test')
        self.assertEqual(judged.call_count, 3)
        delivered.assert_called_once_with(self.directory, 'Newest nudge.', 0)
        self.assertEqual(observed, [None, 'First nudge.', 'Newest nudge.'])
        self.assertFalse((self.directory / 'pending.json').exists())

    def test_interrupted_catch_up_holds_advice_across_worker_delivery_attempts(self):
        original_check = squire.check_once
        class Finished(Exception):
            pass
        for interruption in ('error', 'tell'):
            self.source.write_text(record('first') + record('second') + record('third'))
            squire.save(self.directory / 'cursor.json', {'offset': 0, 'line': 0, 'identity': None})
            def judge(directory, config, notes, data, first, last, **kwargs):
                if first == 1:
                    return dict(self.decision, message='Advice from history.')
                raise ValueError('temporary Codex error')
            def check(*args, **kwargs):
                result = original_check(*args, **kwargs)
                if interruption == 'tell':
                    squire.queue_tell(self.directory, 'Consider this correction.')
                return result
            with self.subTest(interruption=interruption), patch.object(squire, 'INPUT_BUDGET', 1), patch.object(squire, 'pair_dir', return_value=self.directory), patch.object(squire, 'validate_target'), self.judge_context(side_effect=judge), patch.object(squire, 'check_once', side_effect=check), patch.object(squire, 'deliver') as deliver, patch.object(squire, 'wait_for_work', side_effect=Finished), patch('builtins.print'):
                with self.assertRaises(Finished):
                    squire.worker('squire-test')
                self.assertTrue(squire.load(self.directory / 'pending.json')['held'])
                self.assertFalse(squire.deliver_pending(self.directory))
                deliver.assert_not_called()
            self.assertEqual(squire.load(self.directory / 'cursor.json')['line'], 1)
            (self.directory / 'pending.json').unlink()

    def test_silent_catch_up_holds_existing_advice_until_a_current_judgment(self):
        self.source.write_text(record('history') + record('current'))
        squire.save(self.directory / 'pending.json', {'message': 'Earlier advice.', 'tuning_version': 0})
        with patch.object(squire, 'INPUT_BUDGET', 1), patch.object(squire, 'validate_target'), self.judge_context(), patch.object(squire, 'deliver', return_value=True) as deliver, patch('builtins.print'):
            self.assertTrue(squire.check_once(self.directory, catching_up=True))
            self.assertTrue(squire.load(self.directory / 'pending.json')['held'])
            self.assertFalse(squire.deliver_pending(self.directory))
            deliver.assert_not_called()
            self.assertTrue(squire.check_once(self.directory))
            deliver.assert_called_once_with(self.directory, 'Earlier advice.', 0)
        self.assertFalse((self.directory / 'pending.json').exists())

    def test_held_advice_does_not_wake_the_worker_before_its_next_check(self):
        squire.save(self.directory / 'pending.json', {'message': 'Earlier advice.', 'tuning_version': 0, 'held': True})
        clock = [1000]
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(squire.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(squire.time, 'sleep', side_effect=sleep):
            squire.wait_for_work(self.directory, 3)
        self.assertEqual(clock[0], 1003)

    def test_silent_tell_refreshes_retained_advice_tuning_before_delivery(self):
        squire.save(self.directory / 'pending.json', {'message': 'Still relevant advice.', 'tuning_version': 0})
        with squire.locked(self.directory / 'worker.lock'):
            squire.queue_tell(self.directory, 'Why?')
        with self.judge_context(), patch.object(squire, 'tmux', side_effect=self.transport), patch.object(squire.time, 'sleep'), patch('builtins.print'):
            self.assertTrue(squire.process_tell_once(self.directory))
        self.assertFalse((self.directory / 'pending.json').exists())
        notes = squire.load(self.directory / 'notes.json')
        self.assertEqual(notes['said'][0]['message'], 'Still relevant advice.')
        self.assertEqual(notes['tuning'][0]['status'], 'done')
        self.assertEqual([args[-1] for args in self.sent if '-l' not in args], ['Enter'])

    def test_withdrawal_drops_pending_when_there_is_no_valid_replacement(self):
        for message in ('', '   ', '\x7fInvalid advice'):
            squire.save(self.directory / 'cursor.json', {'offset': 0, 'line': 0, 'identity': None})
            squire.save(self.directory / 'pending.json', {'message': 'Obsolete advice.', 'tuning_version': 0})
            decision = dict(self.decision, message=message, withdraw_undelivered=True)
            with self.subTest(message=message), patch.object(squire, 'validate_target'), self.judge_context(decision=decision), patch.object(squire, 'deliver') as deliver, patch('builtins.print'):
                self.assertTrue(squire.check_once(self.directory))
                deliver.assert_not_called()
            self.assertFalse((self.directory / 'pending.json').exists())
            self.assertEqual(squire.load(self.directory / 'cursor.json')['line'], 1)
            self.assertEqual(squire.load(self.directory / 'notes.json')['trajectory'], decision['trajectory'])
        self.assertEqual((self.directory / 'turns.log').read_text().count('Withdrew undelivered advice.'), 3)
        with patch.object(squire, 'log_turn') as log:
            squire.commit_decision(self.directory, squire.load(self.directory / 'notes.json'),
                                   dict(self.decision, withdraw_undelivered=True), tuning_version=0)
        log.assert_not_called()

    def test_new_valid_message_supersedes_even_with_withdrawal_requested(self):
        squire.save(self.directory / 'pending.json', {'message': 'Obsolete advice.', 'tuning_version': 0})
        decision = dict(self.decision, message='New evidence upstream.', withdraw_undelivered=True)
        with patch.object(squire, 'validate_target'), self.judge_context(decision=decision), patch.object(squire.time, 'time', return_value=2000), patch('builtins.print'):
            self.assertTrue(squire.check_once(self.directory, catching_up=True))
        self.assertEqual(squire.load(self.directory / 'pending.json'), {
            'message': 'New evidence upstream.', 'tuning_version': 0, 'since': 2000, 'held': True})
        self.assertNotIn('Withdrew undelivered advice.', (self.directory / 'turns.log').read_text())

    def test_observer_sees_undelivered_advice_and_reason_on_bootstrap_and_resume(self):
        pending = {'message': 'Check upstream.', 'deferred_reason': 'existing human draft',
                   'since': 1000, 'tuning_version': 0, 'retry_after': 2000}
        squire.save(self.directory / 'pending.json', pending)
        payloads = []
        def turn(directory, config, prompt):
            initial = prompt(True)
            payloads.append(json.loads(initial[initial.index('{"mode"'):]))
            payloads.append(json.loads(prompt(False)))
            (directory / 'pending.json').unlink()
            payloads.append(json.loads(prompt(False)))
            return self.decision
        with patch.object(squire, 'codex_turn', side_effect=turn):
            squire.judge(self.directory, self.config, squire.load(self.directory / 'notes.json'), '', None, None)
        for payload in payloads[:2]:
            self.assertEqual(payload['undelivered'], {'message': 'Check upstream.', 'deferred_reason': 'existing human draft', 'since': 1000})
        self.assertIsNone(payloads[2]['undelivered'])

    def test_repeated_deferrals_are_visible_and_reason_changes_reset_the_count(self):
        squire.save(self.directory / 'pending.json', {'message': 'Check upstream.', 'tuning_version': 0, 'since': 1000})
        clock = [1000]
        with patch.object(squire.time, 'time', side_effect=lambda: clock[0]), patch.object(squire, 'deliver', side_effect=squire.PaneBlocked('existing human draft')) as deliver, patch('builtins.print'):
            self.assertFalse(squire.deliver_pending(self.directory))
            self.assertFalse(squire.deliver_pending(self.directory))
            self.assertEqual(deliver.call_count, 1)  # Retry cooldown does not count.
            clock[0] += 360
            self.assertFalse(squire.deliver_pending(self.directory))
        self.assertEqual(squire.load(self.directory / 'pending.json')['deferral_count'], 2)
        for alive in (False, True):
            with patch.object(squire, 'STATE', self.home), patch.object(squire, 'worker_alive', return_value=alive), patch('builtins.print') as output:
                squire.main(['status'])
            self.assertIn('1 undelivered; delivery deferred x2: existing human draft', output.call_args.args[0])
        clock[0] += 360
        with patch.object(squire.time, 'time', side_effect=lambda: clock[0]), patch.object(squire, 'deliver', side_effect=squire.PaneBlocked('copy mode')), patch('builtins.print'):
            self.assertFalse(squire.deliver_pending(self.directory))
        pending = squire.load(self.directory / 'pending.json')
        self.assertEqual(pending['deferral_count'], 1)
        self.assertEqual(pending['deferred_reason'], 'copy mode')
        self.assertEqual(pending['since'], 1000)

    def test_replacement_nudges_preserve_repeated_deferrals_in_status(self):
        clock = [1000]
        decisions = [dict(self.decision, message=message) for message in ('Original advice.', 'Restated advice.')]
        with patch.object(squire.time, 'time', side_effect=lambda: clock[0]), patch.object(squire, 'validate_target'), self.judge_context(side_effect=decisions), patch.object(squire, 'deliver', side_effect=squire.PaneBlocked('existing human draft')) as deliver, patch('builtins.print'):
            self.assertTrue(squire.check_once(self.directory))
            with self.source.open('a') as source:
                source.write(record('new work'))
            clock[0] += 360
            self.assertTrue(squire.check_once(self.directory))
        self.assertEqual(deliver.call_count, 2)
        pending = squire.load(self.directory / 'pending.json')
        self.assertEqual(pending['message'], 'Restated advice.')
        self.assertEqual(pending['deferral_count'], 2)
        with patch.object(squire, 'STATE', self.home), patch('builtins.print') as output:
            squire.main(['status'])
        self.assertIn('1 undelivered; delivery deferred x2: existing human draft', output.call_args.args[0])

    def test_mid_check_tuning_keeps_cursor_and_trajectory_drops_only_message(self):
        squire.save(self.directory / 'pending.json', {'message': 'Earlier advice.', 'tuning_version': 0})
        def judged(*args, **kwargs):
            notes = squire.load(self.directory / 'notes.json')
            notes['tuning'].append({'message': 'Suppress this class.'})
            squire.save(self.directory / 'notes.json', notes)
            return dict(self.decision, message='Old advice')
        with patch.object(squire, 'validate_target'), self.judge_context(side_effect=judged) as judge, patch.object(squire, 'deliver') as deliver, patch('builtins.print'):
            self.assertTrue(squire.check_once(self.directory))
            self.assertFalse(squire.check_once(self.directory))
        self.assertEqual(judge.call_count, 1)
        deliver.assert_not_called()
        self.assertEqual(squire.load(self.directory / 'cursor.json')['line'], 1)
        self.assertEqual(squire.load(self.directory / 'notes.json')['trajectory'], self.decision['trajectory'])
        self.assertFalse((self.directory / 'pending.json').exists())

    def test_three_same_cursor_failures_stop_with_status_reason(self):
        with patch.object(squire, 'validate_target'), self.judge_context(side_effect=ValueError('bad decision')), patch('builtins.print'):
            for _ in range(3):
                with self.assertRaises(ValueError):
                    squire.check_once(self.directory)
        config = squire.load(self.directory / 'config.json')
        self.assertTrue(config['stopped'])
        self.assertIn('three consecutive failures', config['stop_reason'])
        self.assertEqual(squire.load(self.directory / 'cursor.json')['line'], 0)
        with patch.object(squire, 'STATE', self.home), patch('builtins.print') as output:
            squire.main(['status'])
        self.assertIn('bad decision', output.call_args.args[0])

    def test_copy_dialog_recent_activity_and_existing_draft_receive_no_keys(self):
        for blocked in ('copy', 'dialog', 'client', 'draft'):
            self.copy_mode = blocked == 'copy'
            self.screen = 'Type your answer' if blocked == 'dialog' else 'human draft' if blocked == 'draft' else ''
            self.client = '$1\t' + str(squire.time.time()) if blocked == 'client' else ''
            self.sent.clear()
            with self.subTest(blocked=blocked), patch.object(squire, 'tmux', side_effect=self.transport):
                with self.assertRaises(squire.PaneBlocked):
                    squire.deliver(self.directory, 'Check step 1.')
            self.assertEqual(self.sent, [])
        self.assertEqual(squire.load(self.directory / 'notes.json')['said'], [])

    def test_identity_or_exited_harness_never_receives_any_keys(self):
        identities = [
            '%2\t$1\t101\tcodex\t0\t0',
            '%1\t$2\t101\tcodex\t0\t0',
            '%1\t$1\t999\tcodex\t0\t0',
            '%1\t$1\t101\tbash\t0\t0',
        ]
        for identity in identities:
            with self.subTest(identity=identity), patch.object(squire, 'tmux', return_value=identity) as tmux:
                with self.assertRaisesRegex(ValueError, 'identity or harness'):
                    squire.deliver(self.directory, 'Look upstream; this must never execute in bash.')
                self.assertEqual(tmux.call_count, 1)

    def test_wrapped_composers_are_conservatively_deferred(self):
        codex = '› earlier submitted prompt\n\n• Earlier response.\n\n› [squire] Inspect the original anchor before changing\n  another animation setting.\n\n  GPT-6.1-Sol medium · Context 1% used\n'
        claude = 'Claude Code v2.1.301\n────────────────────────────────────\n❯\u00a0[squire] Inspect the original anchor before changing\n  another animation setting.\n────────────────────────────────────\n  Opus 5.5 · medium\n'
        expected = '[squire] Inspect the original anchor before changing another animation setting.'
        with self.assertRaises(squire.PaneBlocked):
            squire.composer(codex, 'codex', 5)
        with self.assertRaises(squire.PaneBlocked):
            squire.composer(claude, 'claude', 3)
        self.assertEqual(squire.composer('› Ask Codex to do anything\n\n  GPT-6.1-Sol', 'codex', 0), '')
        self.assertEqual(squire.composer('────────────────\n❯\u00a0\n────────────────', 'claude', 1), '')
        with self.assertRaises(squire.PaneBlocked):
            squire.composer('No active prompt is identifiable.', 'codex', 0)

    def test_capture_preserves_blank_grid_rows_for_absolute_composer_cursor(self):
        screen = '\n' * 35 + 'Earlier response.\n\n› [squire] Check the upstream anchor\n  before changing animation.\n\n  GPT-6.1-Sol medium\n\n'
        result = type('Result', (), {'stdout': screen})()
        with patch.object(squire.subprocess, 'run', return_value=result):
            captured = squire.tmux('/sock', 'capture-pane', '-p', '-t', '%1')
        self.assertEqual(captured, screen)
        with self.assertRaises(squire.PaneBlocked):
            squire.composer(captured, 'codex', 38)

    def test_titled_claude_composer_delivers_and_preserves_human_drafts(self):
        squire.save(self.directory / 'config.json', dict(self.config, harness='claude'))
        def transport(socket, verb, *args):
            if verb == 'display-message':
                return '%1\t$1\t101\tclaude\t0\t14'
            if verb == 'capture-pane':
                return titled_claude_frame(self.screen)
            return self.transport(socket, verb, *args)
        with patch.object(squire, 'tmux', side_effect=transport), patch.object(squire.time, 'sleep'), patch('builtins.print'):
            self.assertTrue(squire.deliver(self.directory, 'Check upstream.'))
            self.assertEqual(self.sent, [('-t', '%1', '-l', '--', '[squire] Check upstream.'), ('-t', '%1', 'Enter')])
            self.sent.clear()
            self.screen = 'Unsent human draft'
            with self.assertRaisesRegex(squire.PaneBlocked, 'already contains a draft'):
                squire.deliver(self.directory, 'Check upstream.')
            self.assertEqual(self.sent, [])

    def test_claude_title_rule_is_narrow_and_must_bound_the_composer(self):
        for line in ('─' * 60, '━' * 10 + ' A ━', '─' * 51 + ' Athena ─'):
            self.assertTrue(squire.claude_rule(line), line)
        for line in ('     ────', '─── Athena', 'Athena ───', '─── A ─ B ─', '─── ' + 'A' * 63 + ' ─', '─── A\tB ─', '─── A\x1bB ─', '─── A\u200bB ─'):
            self.assertFalse(squire.claude_rule(line), repr(line))
        unknown = '─── Athena ─\nUnknown view\n❯ \n────────'
        for recognize in (squire.normal_frame, squire.composer):
            with self.assertRaises(squire.PaneBlocked):
                recognize(unknown, 'claude', 2)

    def test_blank_rows_cannot_hide_human_draft_at_any_cursor_row(self):
        frames = [
            ('codex', '› \n\n  Human draft below blank input\n\nGPT-6.1-Sol medium', (0, 2)),
            ('codex', '› \n\n  GPT-human draft text\n  More human text\n\nGPT-6.1-Sol medium', (0, 3)),
            ('claude', '────────────────\n❯ \n\n  Human draft below blank input\n────────────────\nOpus 5.5', (1, 3)),
            ('claude', '────────────────\n❯ \n\n  ─ Human draft text\n  More human text\n────────────────\nOpus 5.5', (1, 4)),
        ]
        for harness, frame, cursors in frames:
            for cursor_y in cursors:
                config = dict(self.config, harness=harness)
                squire.save(self.directory / 'config.json', config)
                def transport(socket, verb, *args):
                    if verb == 'display-message':
                        return f'%1\t$1\t101\t{harness}\t0\t{cursor_y}'
                    if verb == 'capture-pane':
                        return frame
                    return ''
                with self.subTest(harness=harness, cursor_y=cursor_y), patch.object(squire, 'tmux', side_effect=transport) as tmux:
                    with self.assertRaises(squire.PaneBlocked):
                        squire.deliver(self.directory, 'Check upstream.')
                    self.assertFalse(any(call.args[1] == 'send-keys' for call in tmux.call_args_list))

    def test_normal_wrapping_needs_no_reconstruction_but_failed_wrapping_stops(self):
        message = 'Inspect /tmp/squire/long-directory/hand-anchor.json before changing animation.'
        for rollback in (False, True):
            self.sent.clear()
            self.screen = ''
            self.client = ''
            def wrapped_transport(socket, verb, *args):
                if verb == 'capture-pane' and self.screen:
                    cut = self.screen.index('hand-anchor') + 4
                    return '› ' + self.screen[:cut] + '\n  ' + self.screen[cut:] + '\n\nGPT-6.1-Sol medium'
                return self.transport(socket, verb, *args)
            def settle(duration):
                self.assertEqual(duration, 0.25)
                if rollback:
                    self.client = '$1\t' + str(squire.time.time())
            with self.subTest(rollback=rollback), patch.object(squire, 'tmux', side_effect=wrapped_transport), patch.object(squire.time, 'sleep', side_effect=settle), patch('builtins.print'):
                if rollback:
                    with self.assertRaises(squire.PaneBlocked):
                        squire.deliver(self.directory, message)
                else:
                    squire.deliver(self.directory, message)
            keys = [args[-1] for args in self.sent if '-l' not in args]
            self.assertEqual(keys, [] if rollback else ['Enter'])
            self.assertEqual(squire.load(self.directory / 'config.json')['stopped'], rollback)

    def test_indented_prompt_markers_cannot_hide_earlier_human_text(self):
        frames = [
            ('codex', '› Important unsent human instructions\n  › \n\n  GPT-6.1-Sol medium', 1),
            ('claude', '────────────────\n❯ Important unsent human instructions\n  ❯ \n────────────────\nOpus 5.5', 2),
        ]
        for harness, frame, cursor_y in frames:
            config = dict(self.config, harness=harness)
            squire.save(self.directory / 'config.json', config)
            def transport(socket, verb, *args):
                if verb == 'display-message':
                    return f'%1\t$1\t101\t{harness}\t0\t{cursor_y}'
                return frame if verb == 'capture-pane' else ''
            with self.subTest(harness=harness), patch.object(squire, 'tmux', side_effect=transport) as tmux:
                with self.assertRaises(squire.PaneBlocked):
                    squire.deliver(self.directory, 'Check upstream.')
                self.assertFalse(any(call.args[1] == 'send-keys' for call in tmux.call_args_list))
            with self.assertRaises(squire.PaneBlocked):
                squire.composer(frame, harness, cursor_y)

    def test_enter_spawn_failure_clears_owned_draft_and_preserves_error_for_retry(self):
        failed = [False]
        def transport(socket, verb, *args):
            if verb == 'send-keys' and args[-1] == 'Enter' and not failed[0]:
                failed[0] = True
                raise OSError('Enter spawn failed')
            return self.transport(socket, verb, *args)
        with patch.object(squire, 'tmux', side_effect=transport), patch.object(squire.time, 'sleep'), patch('builtins.print'):
            with self.assertRaisesRegex(OSError, 'Enter spawn failed'):
                squire.deliver(self.directory, 'Check upstream.')
            self.assertEqual(self.screen, '')
            self.assertEqual(squire.load(self.directory / 'notes.json')['said'], [])
            self.assertFalse(squire.load(self.directory / 'config.json')['stopped'])
            squire.deliver(self.directory, 'Check upstream.')
        self.assertEqual([args[-1] for args in self.sent if '-l' not in args], ['C-u', 'Enter'])
        self.assertEqual(len(squire.load(self.directory / 'notes.json')['said']), 1)

    def test_receipt_failure_after_successful_enter_stops_instead_of_resubmitting(self):
        original_save = squire.save
        def save(path, value):
            if path.name == 'notes.json':
                raise OSError('receipt file unavailable')
            original_save(path, value)
        squire.save(self.directory / 'pending.json', {'message': 'Check upstream.', 'tuning_version': 0})
        with patch.object(squire, 'tmux', side_effect=self.transport), patch.object(squire.time, 'sleep'), patch.object(squire, 'save', side_effect=save), patch('builtins.print'):
            self.assertFalse(squire.deliver_pending(self.directory))
            self.assertFalse(squire.deliver_pending(self.directory))
        self.assertEqual(sum(args[-1] == 'Enter' for args in self.sent), 1)
        self.assertIn('delivery submitted but receipt persistence failed', squire.load(self.directory / 'config.json')['stop_reason'])

    def test_posttyping_unknown_view_never_gets_enter_for_either_harness(self):
        for harness in ('codex', 'claude'):
            squire.save(self.directory / 'config.json', dict(self.config, harness=harness))
            self.sent.clear()
            self.screen = ''
            self.client = ''
            changed = [False]
            def transport(socket, verb, *args):
                if verb == 'display-message':
                    return f'%1\t$1\t101\t{harness}\t0\t' + ('1' if harness == 'claude' else '0')
                if verb == 'capture-pane':
                    if changed[0]:
                        return 'Unrelated view\nNo ordinary composer here\n'
                    return ('────────────────\n❯ ' + self.screen + '\n────────────────\nOpus 5.5') if harness == 'claude' else '› ' + self.screen + '\n\nGPT-6.1-Sol medium'
                return self.transport(socket, verb, *args)
            def settle(duration):
                changed[0] = True
            with self.subTest(harness=harness), patch.object(squire, 'tmux', side_effect=transport), patch.object(squire.time, 'sleep', side_effect=settle), patch('builtins.print'):
                with self.assertRaises(squire.PaneBlocked):
                    squire.deliver(self.directory, 'Check upstream.')
            self.assertEqual(len(self.sent), 1)
            self.assertIn('-l', self.sent[0])
            self.assertIn('retained draft', squire.load(self.directory / 'config.json')['stop_reason'])
            self.assertEqual(squire.load(self.directory / 'notes.json')['said'], [])

    def test_ordinary_wrapped_frames_submit_for_both_harnesses_without_parsing_text(self):
        for harness in ('codex', 'claude'):
            squire.save(self.directory / 'config.json', dict(self.config, harness=harness))
            self.sent.clear()
            self.screen = ''
            self.client = ''
            def transport(socket, verb, *args):
                staged = bool(self.screen)
                if verb == 'display-message':
                    cursor = (2 if staged else 1) if harness == 'claude' else (1 if staged else 0)
                    return f'%1\t$1\t101\t{harness}\t0\t{cursor}'
                if verb == 'capture-pane':
                    body = self.screen[:40] + '\n  ' + self.screen[40:] if staged else ''
                    return ('────────────────\n❯ ' + body + '\n────────────────\nOpus 5.5') if harness == 'claude' else '› ' + body + '\n\nGPT-6.1-Sol medium'
                return self.transport(socket, verb, *args)
            with self.subTest(harness=harness), patch.object(squire, 'tmux', side_effect=transport), patch.object(squire.time, 'sleep'), patch('builtins.print'):
                squire.deliver(self.directory, 'Inspect /tmp/long-directory/hand-anchor.json before changing animation settings.')
            self.assertEqual([args[-1] for args in self.sent if '-l' not in args], ['Enter'])

    def test_question_widgets_from_both_installed_harnesses_are_blocked(self):
        for text in ('Type something.', 'Chat about this', 'Enter to select', 'Type your answer', 'None of the above', 'Use this reset?', 'You have 3 usage limit resets available'):
            self.assertIsNotNone(squire.BLOCKING_DIALOG.search(text))

    def test_other_session_activity_does_not_block_and_message_is_capped(self):
        self.client = '$999\t' + str(squire.time.time())
        message = '  Inspect\n the\t upstream anchor. ' * 50
        with patch.object(squire, 'tmux', side_effect=self.transport), patch.object(squire.time, 'sleep'), patch('builtins.print'):
            squire.deliver(self.directory, message)
        literals = [args[-1] for args in self.sent if '-l' in args]
        self.assertEqual(len(literals), 1)
        self.assertLessEqual(len(literals[0]) - len('[squire] '), 400)
        self.assertNotIn('\n', literals[0])
        self.assertEqual(sum(args[-1] == 'Enter' for args in self.sent), 1)
        with self.assertRaises(ValueError):
            squire.deliver(self.directory, '\x7f' * 9 + '/usage')

    def test_post_settle_activity_clears_only_exact_owned_text_without_submit(self):
        def settle(duration):
            self.assertEqual(duration, 0.25)
            self.client = '$1\t' + str(squire.time.time())
        with patch.object(squire, 'tmux', side_effect=self.transport), patch.object(squire.time, 'sleep', side_effect=settle), patch('builtins.print'):
            with self.assertRaises(squire.PaneBlocked):
                squire.deliver(self.directory, 'Check the anchor.')
        self.assertEqual([args[-1] for args in self.sent if '-l' not in args], ['C-u'])
        self.assertEqual(self.screen, '')
        self.assertFalse(squire.load(self.directory / 'config.json')['stopped'])
        self.assertEqual(squire.load(self.directory / 'notes.json')['said'], [])

    def test_changed_human_draft_or_hidden_modal_never_gets_destructive_cleanup(self):
        for change in ('human', 'modal'):
            config = dict(self.config)
            squire.save(self.directory / 'config.json', config)
            self.sent.clear()
            self.screen = ''
            self.client = ''
            def settle(duration):
                self.screen += ' human addition' if change == 'human' else '\nType your answer'
                if change == 'human':
                    self.client = '$1\t' + str(squire.time.time())
            with self.subTest(change=change), patch.object(squire, 'tmux', side_effect=self.transport), patch.object(squire.time, 'sleep', side_effect=settle), patch('builtins.print'):
                with self.assertRaises(squire.PaneBlocked):
                    squire.deliver(self.directory, 'Check anchor.')
            self.assertEqual(len(self.sent), 1)
            self.assertIn('-l', self.sent[0])
            self.assertIn('retained draft', squire.load(self.directory / 'config.json')['stop_reason'])

    def fake_process(self, scripts, commands, prompts):
        class Process:
            def __init__(inner, command, **kwargs):
                commands.append(command)
                inner.command = command
                inner.script = scripts.pop(0)
                inner.returncode = inner.script.get('code', 0)
            def communicate(inner, prompt):
                prompts.append(prompt)
                if inner.returncode == 0:
                    Path(inner.command[inner.command.index('-o') + 1]).write_text(json.dumps(inner.script.get('decision', self.decision)))
                return inner.script.get('events', ''), inner.script.get('error', '')
        return Process

    def test_full_bootstrap_only_for_new_or_recovered_thread(self):
        commands, prompts = [], []
        scripts = [{'events': json.dumps({'type': 'thread.started', 'thread_id': 'one'})}, {'events': json.dumps({'type': 'thread.started', 'thread_id': 'one'})}, {'code': 1, 'error': 'no rollout found for thread id one'}, {'events': json.dumps({'type': 'thread.started', 'thread_id': 'two'})}]
        notes = squire.load(self.directory / 'notes.json')
        with patch.object(squire.subprocess, 'Popen', self.fake_process(scripts, commands, prompts)), patch('builtins.print'):
            squire.judge(self.directory, self.config, notes, record('first'), 1, 1)
            squire.judge(self.directory, self.config, notes, record('second'), 2, 2)
            squire.judge(self.directory, self.config, notes, '', None, None, request='Why?')
        self.assertIn('Check the upstream anchor.', prompts[0])
        self.assertNotIn('Check the upstream anchor.', prompts[1])
        self.assertNotIn('"trajectory"', prompts[1])
        self.assertIn('Check the upstream anchor.', prompts[3])
        self.assertEqual(commands[1][commands[1].index('resume') + 1], 'one')
        self.assertNotIn('--ephemeral', commands[0])
        self.assertIn('read-only', commands[0])

    def test_first_tell_bootstrap_has_actual_path_linecount_and_legacy_guard(self):
        cursor = squire.load(self.directory / 'cursor.json')
        cursor['line'] = 127
        squire.save(self.directory / 'cursor.json', cursor)
        captured = []
        def turn(directory, config, prompt):
            captured.append(prompt(True))
            return self.decision
        with patch.object(squire, 'codex_turn', side_effect=turn):
            squire.judge(self.directory, self.config, squire.load(self.directory / 'notes.json'), '', None, None, request='What are you watching?')
        payload = json.loads(captured[0][captured[0].index('{"mode"'):])
        self.assertEqual(payload['transcript_line_count'], 127)
        self.assertEqual(payload['transcript_path'], str(self.source))
        self.assertEqual(payload['observer_mode'], 'guard')

    def test_network_failure_does_not_create_replacement_thread(self):
        squire.save(self.directory / 'thread.json', {'thread_id': 'existing'})
        scripts = [{'code': 1, 'error': 'network error'}]
        commands = []
        with patch.object(squire.subprocess, 'Popen', self.fake_process(scripts, commands, [])):
            with self.assertRaises(ValueError):
                squire.codex_turn(self.directory, self.config, 'delta')
        self.assertEqual(len(commands), 1)
        self.assertEqual(squire.load(self.directory / 'thread.json')['thread_id'], 'existing')

    def test_decision_requires_strings_and_a_boolean_withdrawal_field(self):
        invalid = [dict(self.decision, withdraw_undelivered=value) for value in ('false', 0, 1, None, [])]
        invalid += [dict(self.decision, message=False), dict(self.decision, trajectory=7),
                    {key: value for key, value in self.decision.items() if key != 'withdraw_undelivered'},
                    dict(self.decision, extra=True), [], None]
        cases = [(True, self.decision), (True, dict(self.decision, withdraw_undelivered=True))]
        cases += [(False, decision) for decision in invalid]
        for valid, decision in cases:
            scripts = [{'events': json.dumps({'type': 'thread.started', 'thread_id': 'one'}), 'decision': decision}]
            with self.subTest(decision=decision), patch.object(squire.subprocess, 'Popen', self.fake_process(scripts, [], [])), patch('builtins.print'):
                if not valid:
                    with self.assertRaisesRegex(ValueError, 'invalid decision JSON'):
                        squire.codex_turn(self.directory, self.config, 'Check the run.')
                else:
                    self.assertEqual(squire.codex_turn(self.directory, self.config, 'Check the run.'), decision)
        schema = squire.load(self.directory / 'schema.json')
        self.assertEqual(schema['properties']['withdraw_undelivered']['type'], 'boolean')
        self.assertIn('withdraw_undelivered', schema['required'])

    def test_dead_worker_tell_fails_and_active_worker_accepts_once(self):
        with self.assertRaisesRegex(ValueError, 'worker is not running'):
            squire.queue_tell(self.directory, 'Why?')
        with squire.locked(self.directory / 'worker.lock'):
            squire.queue_tell(self.directory, 'Why?')
        with self.judge_context(decision=dict(self.decision, message='Upstream evidence.')) as judge, patch.object(squire, 'deliver', return_value=True), patch('builtins.print'):
            self.assertTrue(squire.process_tell_once(self.directory))
            self.assertFalse(squire.process_tell_once(self.directory))
        self.assertEqual(judge.call_count, 1)
        self.assertEqual(squire.load(self.directory / 'notes.json')['tuning'][0]['status'], 'done')
        self.assertEqual(squire.load(self.directory / 'cursor.json')['line'], 0)

    def test_recent_start_is_one_bounded_suffix_ending_at_complete_eof(self):
        self.source.write_text(record('old' * 400) * 16000 + '{"partial":')
        complete, before = squire.recent_start(self.source, 'codex')
        self.assertEqual(complete['line'], 16000)
        self.assertLess(complete['offset'], self.source.stat().st_size)
        self.assertGreater(before['line'], 15000)
        prepared = list(squire.live_batches(self.source, before, complete['offset'], 'codex'))
        self.assertEqual(len(prepared), 1)
        self.assertEqual(prepared[0]['last'], 16000)

    def test_bootstrap_partial_tail_and_future_append_never_repay_old_window(self):
        self.source.write_text(record('old') + '{"partial":')
        end, start = squire.recent_start(self.source, 'codex')
        squire.save(self.directory / 'cursor.json', end)
        squire.save(self.directory / 'bootstrap.json', {'start': start, 'end': end})
        with self.source.open('a') as handle:
            handle.write('true}\n' + record('new'))
        clock, judged = [1000], []
        def judge(*args, **kwargs):
            judged.append(kwargs['rendered'])
            return self.decision
        class Finished(Exception):
            pass
        def wait(directory, timeout):
            self.assertFalse((directory / 'bootstrap.json').exists())
            if len(judged) == 2:
                raise Finished()
            clock[0] += timeout
        with patch.object(squire, 'pair_dir', return_value=self.directory), patch.object(squire, 'validate_target'), patch.object(squire.time, 'monotonic', side_effect=lambda: clock[0]), self.judge_context(side_effect=judge), patch.object(squire, 'wait_for_work', side_effect=wait), patch('builtins.print'):
            with self.assertRaises(Finished):
                squire.worker('squire-test')
        self.assertIn('old', judged[0])
        self.assertNotIn('new', judged[0])
        self.assertIn('new', judged[1])
        self.assertNotIn('old', judged[1])

    def test_blocked_initial_check_keeps_bootstrap_until_success(self):
        end, start = squire.recent_start(self.source, 'codex')
        squire.save(self.directory / 'cursor.json', end)
        squire.save(self.directory / 'bootstrap.json', {'start': start, 'end': end})
        clock, waits = [1000], []
        class Finished(Exception):
            pass
        def wait(directory, timeout):
            waits.append(timeout)
            if len(waits) == 1:
                self.assertTrue((directory / 'bootstrap.json').exists())
                clock[0] += timeout
            else:
                self.assertFalse((directory / 'bootstrap.json').exists())
                raise Finished()
        with patch.object(squire, 'pair_dir', return_value=self.directory), patch.object(squire, 'validate_target', side_effect=[squire.PaneBlocked('copy mode'), None]), patch.object(squire.time, 'monotonic', side_effect=lambda: clock[0]), self.judge_context() as judge, patch.object(squire, 'wait_for_work', side_effect=wait), patch('builtins.print'):
            with self.assertRaises(Finished):
                squire.worker('squire-test')
        self.assertEqual(judge.call_count, 1)

    def test_worker_missing_file_failures_stop_after_three_without_judgment(self):
        self.source.unlink()
        clock = [1000]
        def wait(directory, timeout):
            clock[0] += timeout
        with patch.object(squire, 'pair_dir', return_value=self.directory), patch.object(squire.time, 'monotonic', side_effect=lambda: clock[0]), self.judge_context() as judge, patch.object(squire, 'wait_for_work', side_effect=wait), patch('builtins.print'):
            squire.worker('squire-test')
        judge.assert_not_called()
        self.assertEqual(squire.load(self.directory / 'failure.json')['count'], 3)
        self.assertIn('three consecutive failures', squire.load(self.directory / 'config.json')['stop_reason'])

    def test_pending_tell_interrupts_backlog_before_next_judgment(self):
        self.source.write_text(''.join(record('x' * 9000, 1000 + i) for i in range(40)))
        calls = []
        def judge(directory, config, notes, data, first, last, request=None, **kwargs):
            calls.append(('tell', request) if request else ('check', first))
            if request:
                config['stopped'] = True
                squire.save(directory / 'config.json', config)
            else:
                squire.queue_tell(directory, 'Do not repeat this class of advice.')
            return self.decision
        with patch.object(squire, 'pair_dir', return_value=self.directory), patch.object(squire, 'validate_target'), self.judge_context(side_effect=judge), patch('builtins.print'):
            squire.worker('squire-test')
        self.assertEqual(calls, [('check', 1), ('tell', 'Do not repeat this class of advice.')])
        self.assertLess(squire.load(self.directory / 'cursor.json')['line'], 40)

    def test_bootstrap_never_rejudges_after_delivery_oserror_or_invalid_advice(self):
        for failure in ('transport', 'advice'):
            self.source.write_text(record('existing history') * 223)
            end, start = squire.recent_start(self.source, 'codex')
            squire.save(self.directory / 'cursor.json', end)
            squire.save(self.directory / 'bootstrap.json', {'start': start, 'end': end})
            (self.directory / 'pending.json').unlink(missing_ok=True)
            clock, waits = [1000], []
            class Finished(Exception):
                pass
            def wait(directory, timeout):
                self.assertFalse((directory / 'bootstrap.json').exists())
                waits.append(timeout)
                if len(waits) == 2:
                    raise Finished()
                clock[0] += timeout
            decision = dict(self.decision, message='Check upstream.' if failure == 'transport' else '\x7fInvalid advice')
            with self.subTest(failure=failure), patch.object(squire, 'pair_dir', return_value=self.directory), patch.object(squire, 'validate_target'), patch.object(squire.time, 'monotonic', side_effect=lambda: clock[0]), patch.object(squire.time, 'time', side_effect=lambda: clock[0]), self.judge_context(decision=decision) as judge, patch.object(squire, 'deliver', side_effect=[OSError('temporary tmux spawn failure'), True]) as deliver, patch.object(squire, 'wait_for_work', side_effect=wait), patch('builtins.print'):
                with self.assertRaises(Finished):
                    squire.worker('squire-test')
            self.assertEqual(judge.call_count, 1)
            self.assertEqual(squire.load(self.directory / 'cursor.json'), end)
            self.assertEqual(squire.load(self.directory / 'notes.json')['trajectory'], decision['trajectory'])
            self.assertEqual(deliver.call_count, 2 if failure == 'transport' else 0)

    def test_three_transport_failures_stop_without_repaying_judgment(self):
        squire.save(self.directory / 'pending.json', {'message': 'Check upstream.', 'tuning_version': 0})
        with patch.object(squire, 'deliver', side_effect=OSError('tmux unavailable')) as deliver, self.judge_context() as judge, patch('builtins.print'):
            for _ in range(3):
                pending = squire.load(self.directory / 'pending.json')
                pending['retry_after'] = 0
                squire.save(self.directory / 'pending.json', pending)
                self.assertFalse(squire.deliver_pending(self.directory))
            self.assertFalse(squire.deliver_pending(self.directory))
        self.assertEqual(deliver.call_count, 3)
        judge.assert_not_called()
        self.assertEqual(squire.load(self.directory / 'failure.json')['count'], 3)
        self.assertIn('tmux unavailable', squire.load(self.directory / 'config.json')['stop_reason'])

    def test_empty_and_partial_only_startup_later_observe_appended_conversation(self):
        for partial in (False, True):
            native = record('First available conversation.')
            split = len(native) // 2 if partial else 0
            self.source.write_text(native[:split])
            end, start = squire.recent_start(self.source, 'codex')
            squire.save(self.directory / 'cursor.json', end)
            squire.save(self.directory / 'bootstrap.json', {'start': start, 'end': end})
            clock, waits, judged = [1000], [], []
            class Finished(Exception):
                pass
            def judge(*args, **kwargs):
                judged.append(kwargs['rendered'])
                return self.decision
            def wait(directory, timeout):
                self.assertFalse((directory / 'bootstrap.json').exists())
                waits.append(timeout)
                if len(waits) == 1:
                    self.assertEqual(judged, [])
                    with self.source.open('a') as handle:
                        handle.write(native[split:])
                    clock[0] += timeout
                else:
                    raise Finished()
            with self.subTest(partial=partial), patch.object(squire, 'pair_dir', return_value=self.directory), patch.object(squire, 'validate_target'), patch.object(squire.time, 'monotonic', side_effect=lambda: clock[0]), self.judge_context(side_effect=judge), patch.object(squire, 'wait_for_work', side_effect=wait), patch('builtins.print'):
                with self.assertRaises(Finished):
                    squire.worker('squire-test')
            self.assertEqual(len(judged), 1)
            self.assertIn('First available conversation.', judged[0])
            self.assertEqual(squire.load(self.directory / 'cursor.json')['line'], 1)

    def test_start_validates_target_and_worker_before_return(self):
        target = dict(socket='/sock', pane='%1', session='$1', window='@1', pane_pid='101', cwd=str(self.home))
        args = squire.argparse.Namespace(plan='Observe', plan_file=None, transcript=str(self.source), harness='codex', model='gpt-6.1-sol', effort='medium', interval=360, observer_mode='assist')
        state = self.home / 'state with spaces'
        codex_home = str(self.home / 'codex auth $(literal)')
        with patch.object(squire, 'STATE', state), patch.dict(os.environ, CODEX_HOME=codex_home), patch.object(squire, 'detect_target', return_value=target), patch.object(squire, 'validate_target') as valid, patch.object(squire, 'worker_alive', return_value=True), patch.object(squire, 'tmux', side_effect=['%9', '@9']) as transport, patch('builtins.print'):
            squire.start_pair(args)
        self.assertEqual(valid.call_count, 2)
        stored = squire.load(next(state.glob('squire-*/config.json')))
        self.assertEqual(stored['observer_mode'], 'assist')
        worker_command = shlex.split(transport.call_args_list[0].args[-1])
        self.assertEqual(worker_command[0], 'env')
        self.assertIn('SQUIRE_STATE_DIR=' + str(state), worker_command)
        self.assertIn('CODEX_HOME=' + codex_home, worker_command)
        self.assertIn('PATH=' + os.environ['PATH'], worker_command)
        with patch.object(squire, 'STATE', self.home / 'other'), patch.object(squire, 'detect_target', return_value=target), patch.object(squire, 'validate_target', side_effect=ValueError('wrong harness')), patch.object(squire, 'tmux') as transport:
            with self.assertRaises(ValueError):
                squire.start_pair(args)
            transport.assert_not_called()

    def test_worker_codex_home_overrides_stale_tmux_environment(self):
        target = dict(socket='/sock', pane='%1', session='$1', window='@1', pane_pid='101', cwd=str(self.home))
        args = squire.argparse.Namespace(plan='Observe', plan_file=None, transcript=str(self.source), harness='codex', model='gpt-6.1-sol', effort='medium', interval=360)
        for custom in (None, os.path.relpath(self.home / 'relative auth')):
            caller_env = dict(os.environ, HOME=str(self.home))
            caller_env.pop('CODEX_HOME', None)
            if custom is not None:
                caller_env['CODEX_HOME'] = custom
            expected = self.home / '.codex' if custom is None else Path(custom).resolve()
            transcript = expected / 'sessions/rollout-thread1.jsonl'
            transcript.parent.mkdir(parents=True, exist_ok=True)
            transcript.touch()
            state = self.home / ('default-state' if custom is None else 'relative-state')
            with self.subTest(custom=custom), patch.dict(os.environ, caller_env, clear=True), patch.object(squire, 'STATE', state), patch.object(squire, 'detect_target', return_value=target), patch.object(squire, 'validate_target'), patch.object(squire, 'worker_alive', return_value=True), patch.object(squire, 'tmux', side_effect=['%9', '@9']) as transport, patch('builtins.print'):
                squire.start_pair(args)
                discovered = squire.discover_transcript('codex', '/work', dict(caller_env, CODEX_THREAD_ID='thread1'))[1]
            self.assertEqual(discovered, transcript)
            command = shlex.split(transport.call_args_list[0].args[-1])
            worker_env = dict(item.split('=', 1) for item in command[1:command.index(squire.sys.executable)])
            # Applying the generated env command must override the server's old
            # home, even when the caller relied on the default or a relative path.
            inherited = dict(CODEX_HOME='/stale-tmux-server-home')
            inherited.update(worker_env)
            self.assertEqual(inherited['CODEX_HOME'], str(expected))
            self.assertTrue(Path(inherited['CODEX_HOME']).is_absolute())

    def test_replay_uses_shared_batches_and_explicit_snapshot_offset(self):
        self.source.write_text(record('past', 500) + record('first', 1000) + record('second', 1400) + record('future', 1800))
        output = self.home / 'replay.jsonl'
        args = squire.argparse.Namespace(plan='Observe', plan_file=None, transcript=str(self.source), start_line=2, end_line=3, chunk_lines=1, interval=360, output=str(output), model='gpt-6.1-sol', effort='medium', observer_mode='assist')
        seen = []
        def judge(directory, config, notes, data, first, last, **kwargs):
            snapshot = Path(config['transcript']).read_text().splitlines()
            offset = config['snapshot_line_offset']
            self.assertEqual(config['observer_mode'], 'assist')
            seen.append((first, last, snapshot[first - offset - 1:last - offset], kwargs['rendered']))
            self.assertNotIn('future', '\n'.join(snapshot))
            self.assertNotIn('past', '\n'.join(snapshot))
            return dict(self.decision, withdraw_undelivered=True)
        with self.judge_context(side_effect=judge), patch('builtins.print'):
            squire.replay(args)
        self.assertEqual([(first, last) for first, last, _, _ in seen], [(2, 2), (3, 3)])
        self.assertIn('first', seen[0][2][0])
        self.assertIn('second', seen[1][2][0])
        self.assertTrue(all(json.loads(line)['withdraw_undelivered'] is True for line in output.read_text().splitlines()))

    def test_stop_race_tolerates_only_verified_observer_disappearance(self):
        config = dict(self.config, observer_pane='%9', observer_window='@9')
        squire.save(self.directory / 'config.json', config)
        error = squire.subprocess.CalledProcessError(1, ['tmux', 'kill-pane'])
        with patch.object(squire, 'tmux', side_effect=['%9\t@9', error, '%1\t@1']):
            squire.stop_pair(self.directory)
        with patch.object(squire, 'tmux', return_value='%9\t@99'):
            with self.assertRaises(ValueError):
                squire.stop_pair(self.directory)
        with patch.object(squire, 'tmux', side_effect=['%9\t@9', error, '%9\t@9']):
            with self.assertRaises(squire.subprocess.CalledProcessError):
                squire.stop_pair(self.directory)

    def test_cli_defaults_modes_and_no_public_say(self):
        seen = []
        with patch.object(squire, 'STATE', self.home), patch.object(squire, 'start', side_effect=lambda args: seen.append(args)), patch.object(squire, 'replay', side_effect=lambda args: seen.append(args)):
            squire.main(['start', '--plan', 'Observe'])
            squire.main(['replay', str(self.source), '--plan', 'Observe', '--output', 'out', '--mode', 'assist'])
        self.assertEqual([(args.observer_mode, args.interval, args.model, args.effort) for args in seen], [('guard', 360, 'gpt-6.1-sol', 'medium'), ('assist', 360, 'gpt-6.1-sol', 'medium')])
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            squire.main(['say', 'squire-test', 'hello'])


if __name__ == '__main__':
    unittest.main()
