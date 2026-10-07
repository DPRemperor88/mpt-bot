"""Без токена, сети и рабочей БД: HTML fixtures, отдельные SQLite и fake Telegram."""
import asyncio
import html
import json
import os
import re
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///:memory:'
os.environ['BOT_TOKEN'] = '123456:TEST_TOKEN'
os.environ['ADMIN_IDS'] = '999'

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Message, Update
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import delivery
import parser
import parser_changes
import services
import ui
from database import crud
from database.migrations import migrate
from database.models import ActionLog, Delivery, Homework, HomeworkFile, _utcnow
from handlers import admin, user
from handlers.states import AddHomework
from main import ThrottlingMiddleware

FIXTURES = Path(__file__).parent / 'fixtures'


class ParserTests(unittest.TestCase):
    def test_multiday_changes(self):
        data = parser_changes.parse_changes((FIXTURES / 'changes.html').read_text(encoding='utf-8'), 'ТЕСТ-1')
        self.assertEqual(data['dates'], ['2026-10-07', '2026-10-08'])
        self.assertEqual([c['date'] for c in data['changes']], data['dates'])

    def test_bad_changes_page_is_not_empty_snapshot(self):
        for source in ['<h1>Maintenance</h1>', '<h4>Замены на 07.10.2026</h4><p>Loading failed</p>']:
            with self.subTest(source=source), self.assertRaises(ValueError):
                parser_changes.parse_changes(source, 'ТЕСТ-1')

    def test_empty_group_on_valid_page(self):
        data = parser_changes.parse_changes((FIXTURES / 'changes.html').read_text(encoding='utf-8'), 'OTHER')
        self.assertEqual(data['changes'], [])

    def test_week_variants_and_cancelled_first_lesson(self):
        data = parser.parse_schedule((FIXTURES / 'schedule.html').read_text(encoding='utf-8'), 'ТЕСТ-1')
        data['anchor_date'] = '2026-10-05'
        target = date(2026, 10, 5)
        self.assertEqual([x['number'] for x in services.effective_lessons(data, target, {})], [1, 2])
        self.assertEqual([x['number'] for x in services.effective_lessons(data, target + timedelta(days=7), {})], [2])
        changes = {1: 'Занятие отменено с последующей отработкой'}
        self.assertEqual([x['number'] for x in services.effective_lessons(data, target, changes)], [2])
        self.assertIsNone(services.subject_pair_number(data, target, 'Математика', changes))

    def test_unknown_week_empty_schedule_and_partial_group_rejected(self):
        source = (FIXTURES / 'schedule.html').read_text(encoding='utf-8')
        for value, group in [(source.replace('Числитель', '?'), 'ТЕСТ-1'),
                             (source.replace('<td>', '<th>').replace('</td>', '</th>'), 'ТЕСТ-1'),
                             (source, 'ТЕСТ')]:
            with self.subTest(group=group), self.assertRaises(ValueError):
                parser.parse_schedule(value, group)

    def test_html_splitting_preserves_text_and_balanced_tags(self):
        original = '😀 <vector> & текст ' * 800
        chunks = delivery.split_html('<b>' + html.escape(original) + '</b>')
        self.assertGreater(len(chunks), 1)
        recovered = ''
        for chunk in chunks:
            self.assertTrue(chunk.startswith('<b>') and chunk.endswith('</b>'))
            plain = html.unescape(re.sub('<[^>]*>', '', chunk))
            self.assertLessEqual(len(plain.encode('utf-16-le')) // 2, 3500)
            recovered += plain
        self.assertEqual(recovered, original)


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine('sqlite+aiosqlite:///' + Path(self.tmp.name, 'test.db').as_posix())
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session_patch = patch.object(crud, 'SessionLocal', self.sessions)
        self.session_patch.start()
        async with self.engine.begin() as conn:
            await migrate(conn)
        self.bot = NS(send_message=AsyncMock(), send_photo=AsyncMock(), send_document=AsyncMock())

    async def asyncTearDown(self):
        self.session_patch.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def rows(self, model):
        async with self.sessions() as session:
            return list((await session.execute(select(model).order_by(model.id))).scalars())

    async def test_migration_preserves_existing_data_and_is_repeatable(self):
        await crud.get_or_create_user(42)
        async with self.engine.begin() as conn:
            await conn.execute(text('DROP TABLE deliveries'))
            await conn.execute(text('DROP TABLE schema_migrations'))
            await migrate(conn)
            await migrate(conn)
            versions = (await conn.execute(text('SELECT version FROM schema_migrations ORDER BY version'))).scalars().all()
        self.assertEqual(versions, [1, 2])
        self.assertIsNotNone(await crud.get_user(42))

    async def test_atomic_homework_files_log_and_delivery(self):
        await crud.get_or_create_user(42)
        hw = await services.create_homework('C++ <vector>', 'a < b & c', date(2026, 10, 7), 999, [('one', 'photo'), ('two', 'document')])
        self.assertEqual(len(await crud.list_homework_files(hw.id)), 2)
        self.assertEqual(len(await self.rows(ActionLog)), 1)
        queue = await self.rows(Delivery)
        self.assertEqual(len(queue), 1)
        self.assertIn('&lt;vector&gt;', json.loads(queue[0].payload_json)[0]['text'])
        # Ошибка очереди откатывает и ДЗ, и файлы, и журнал.
        with self.assertRaises(IntegrityError):
            await crud.add_homework('Fail', 'x', date(2026, 10, 8), files=[('x', 'photo')],
                deliveries=[{'event_key': 'bad', 'chat_id': None, 'payload_json': '[]'}])
        self.assertEqual(len(await self.rows(Homework)), 1)
        self.assertEqual(len(await self.rows(HomeworkFile)), 2)
        self.assertEqual(len(await self.rows(ActionLog)), 1)

    async def test_changes_new_date_correction_removal_and_restart(self):
        await crud.get_or_create_user(42)
        change = {'lesson': '1', 'replace_from': 'A', 'replace_to': 'B', 'date': '2026-10-07'}
        async def refresh(day, changes):
            data = {'date': day, 'dates': [day], 'changes': changes}
            with patch.object(services.parser_changes, 'fetch_changes', AsyncMock(return_value=data)):
                return await services.refresh_changes(self.bot)
        self.assertEqual(await refresh('2026-10-07', [change]), 0)
        change = dict(change, date='2026-10-08')
        self.assertEqual(await refresh('2026-10-08', [change]), 1)
        self.assertEqual(await refresh('2026-10-08', [change]), 0)
        corrected = dict(change, replace_to='C')
        self.assertEqual(await refresh('2026-10-08', [corrected]), 1)
        self.assertEqual(await refresh('2026-10-08', []), 1)
        self.assertEqual(len(await self.rows(Delivery)), 3)
        await self.engine.dispose()  # восстановление очереди через новые соединения
        await delivery.drain(self.bot)
        self.assertEqual(self.bot.send_message.await_count, 3)
        self.assertTrue(all(r.status == 'sent' for r in await self.rows(Delivery)))

    async def test_failed_parse_preserves_cache(self):
        await crud.save_changes('ТЕСТ', '2026-10-07', '[]')
        with patch.object(services, 'GROUP_NAME', 'ТЕСТ'), patch.object(services.parser_changes, 'fetch_changes', AsyncMock(side_effect=ValueError('bad html'))):
            with self.assertRaises(ValueError):
                await services.refresh_changes(self.bot)
        self.assertEqual((await crud.get_changes('ТЕСТ'))['date'], '2026-10-07')

    async def test_change_snapshot_rolls_back_with_queue(self):
        await crud.save_changes('ТЕСТ', '2026-10-07', '[]')
        with self.assertRaises(IntegrityError):
            await crud.save_changes('ТЕСТ', '2026-10-08', '[{"lesson": "1"}]', deliveries=[
                {'event_key': 'bad', 'chat_id': None, 'payload_json': '[]'}])
        self.assertEqual((await crud.get_changes('ТЕСТ'))['date'], '2026-10-07')
        self.assertEqual(await self.rows(Delivery), [])

    async def test_retry_resumes_after_successful_text_without_duplicate(self):
        await crud.enqueue(services.delivery_rows('test', [42], 'Hello', [('photo', 'photo')]))
        self.bot.send_photo.side_effect = TelegramNetworkError(method=SendMessage(chat_id=42, text='x'), message='offline')
        await delivery.drain(self.bot)
        row = (await self.rows(Delivery))[0]
        self.assertEqual((row.status, row.cursor, row.attempts), ('pending', 1, 1))
        self.bot.send_photo.side_effect = None
        await crud.update_delivery(row.id, next_attempt_at=_utcnow())
        await delivery.drain(self.bot)
        self.assertEqual(self.bot.send_message.await_count, 1)
        self.assertEqual((await self.rows(Delivery))[0].status, 'sent')

    async def test_legacy_and_multiday_cache_filter_by_date(self):
        await crud.save_changes(services.GROUP_NAME, '2026-10-07', json.dumps([
            {'lesson': '1', 'replace_to': 'Legacy'},
            {'lesson': '2-3', 'replace_to': 'Tomorrow', 'date': '2026-10-08'},
        ]))
        self.assertEqual(await services.get_changes_map(date(2026, 10, 7)), {1: 'Legacy'})
        self.assertEqual(await services.get_changes_map(date(2026, 10, 8)), {2: 'Tomorrow', 3: 'Tomorrow'})

    async def test_rate_limit_and_blocked_recipient(self):
        await crud.enqueue(services.delivery_rows('test', [42, 43], 'Hello'))
        method = SendMessage(chat_id=42, text='x')
        self.bot.send_message.side_effect = TelegramRetryAfter(method=method, message='limit', retry_after=60)
        await delivery.drain(self.bot)
        rows = await self.rows(Delivery)
        self.assertEqual(rows[0].status, 'pending')
        self.assertGreater(rows[0].next_attempt_at, _utcnow() + timedelta(seconds=50))
        self.assertEqual(self.bot.send_message.await_count, 1)
        await delivery.drain(self.bot)
        self.assertEqual(self.bot.send_message.await_count, 1)
        await crud.set_meta('delivery_pause_until', _utcnow().isoformat())
        self.bot.send_message.side_effect = TelegramForbiddenError(method=method, message='blocked')
        await delivery.drain(self.bot)
        self.assertEqual((await self.rows(Delivery))[1].status, 'failed')

    async def test_retry_keeps_order_for_recipient_without_blocking_others(self):
        await crud.enqueue(services.delivery_rows('old', [42], 'Old') +
                           services.delivery_rows('new', [42, 43], 'New'))
        rows = await self.rows(Delivery)
        await crud.update_delivery(rows[0].id, next_attempt_at=_utcnow() + timedelta(hours=1))
        await delivery.drain(self.bot)
        self.bot.send_message.assert_awaited_once_with(43, 'New')

    async def test_multiple_homework_items_and_timestamp(self):
        target = date(2026, 10, 5)
        schedule = {'0': [{'number': 1, 'variants': {'числитель': {'subject': 'Math', 'teacher': 'Teacher'}}}]}
        await crud.save_schedule(services.GROUP_NAME, json.dumps(schedule), target.isoformat(), 'числитель')
        old_time = (await crud.get_schedule(services.GROUP_NAME))['updated_at']
        await crud.save_schedule(services.GROUP_NAME, json.dumps(schedule), target.isoformat(), 'числитель')
        self.assertGreater((await crud.get_schedule(services.GROUP_NAME))['updated_at'], old_time)
        for i in range(2):
            await crud.add_homework('Math', f'Task {i}', target, files=[(str(i), 'photo')])
        text_value, items = await services.schedule_day(target)
        self.assertIn('Task 0', text_value)
        self.assertIn('Task 1', text_value)
        self.assertEqual(len(items), 2)

    async def test_morning_uses_active_week_and_queues_once(self):
        target = date(2026, 10, 12)
        data = parser.parse_schedule((FIXTURES / 'schedule.html').read_text(encoding='utf-8'), 'ТЕСТ-1')
        await crud.save_schedule(services.GROUP_NAME, json.dumps(data['days']), '2026-10-05', 'числитель')
        await crud.get_or_create_user(42)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 12, 9, 15, tzinfo=tz)
        with patch.object(services, 'datetime', Clock):
            self.assertTrue(await services.morning_post(self.bot))
            self.assertFalse(await services.morning_post(self.bot))
        self.assertEqual(len(await self.rows(Delivery)), 1)


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def message(self, **kwargs):
        return Message(message_id=1, date=datetime.now(timezone.utc), chat={'id': 42, 'type': 'private'},
                       from_user={'id': 42, 'is_bot': False, 'first_name': '<Alice>'}, **kwargs)

    async def test_album_not_throttled(self):
        throttle = ThrottlingMiddleware(0.8)
        handler = AsyncMock()
        message = self.message(photo=[{'file_id': 'x', 'file_unique_id': 'x', 'width': 1, 'height': 1}], media_group_id='album')
        data = {'event_from_user': message.from_user, 'raw_state': AddHomework.text.state}
        for _ in range(3):
            await throttle(handler, message, data)
        self.assertEqual(handler.await_count, 3)

    async def test_throttled_button_is_acknowledged(self):
        throttle = ThrottlingMiddleware(0.8)
        callback = CallbackQuery(id='x', from_user={'id': 42, 'is_bot': False, 'first_name': 'Test'},
                                 chat_instance='x', data='hwdone', message=self.message(text='Menu'))
        handler = AsyncMock()
        with patch.object(CallbackQuery, 'answer', new=AsyncMock()) as answer:
            await throttle(handler, callback, {'event_from_user': callback.from_user})
            await throttle(handler, callback, {'event_from_user': callback.from_user})
            self.assertEqual(handler.await_count, 1)
            answer.assert_awaited_once()

    async def test_revoked_role_cannot_finish_broadcast(self):
        state = NS(clear=AsyncMock())
        event = NS(from_user=NS(id=42), answer=AsyncMock())
        handler = AsyncMock()
        with patch.object(admin, 'get_role', AsyncMock(return_value='student')):
            await admin.AdminAccessMiddleware()(handler, event, {'handler': NS(callback=admin.broadcast_do), 'state': state})
        handler.assert_not_awaited()
        state.clear.assert_awaited_once()

    async def test_menu_and_cancel_clear_state(self):
        for callback in [user.cmd_menu, user.cmd_start, user.cmd_cancel]:
            state = NS(clear=AsyncMock())
            with patch.object(user, '_greet', AsyncMock()):
                await callback(self.message(text='/menu'), state)
            state.clear.assert_awaited_once()

    async def test_concurrent_album_through_dispatcher_keeps_all_files(self):
        bot = Bot('123456:TEST_TOKEN')
        dp = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
        dp.message.middleware(ThrottlingMiddleware(0.8))
        dp.include_router(admin.admin_router)
        state = FSMContext(storage=dp.storage, key=StorageKey(bot_id=bot.id, chat_id=42, user_id=42))
        await state.set_state(AddHomework.text)
        await state.set_data({'files': [], 'hw_text': ''})
        try:
            with patch.object(admin, 'get_role', AsyncMock(return_value='moderator')), \
                 patch.object(admin, '_refresh_buffer', AsyncMock()), \
                 patch.object(ui, 'delete_safe', AsyncMock()):
                updates = [Update(update_id=i, message=self.message(
                    photo=[{'file_id': str(i), 'file_unique_id': str(i), 'width': 1, 'height': 1}],
                    media_group_id='album')) for i in range(3)]
                await asyncio.gather(*(dp.feed_update(bot, update) for update in updates))
            self.assertEqual({item[0] for item in (await state.get_data())['files']}, {'0', '1', '2'})
        finally:
            await dp.storage.close()
            await dp.fsm.events_isolation.close()
            await bot.session.close()


if __name__ == '__main__':
    unittest.main()
