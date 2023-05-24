# -*- coding: utf-8 -*-
from flectra.addons.account.tests.common import AccountTestInvoicingCommon
from flectra.tests import tagged
from flectra.tests.common import Form, TransactionCase
from flectra import fields, api, SUPERUSER_ID
from flectra.exceptions import ValidationError
from flectra.tools import mute_logger

from dateutil.relativedelta import relativedelta
from freezegun import freeze_time
from functools import reduce
import json
import psycopg2


@tagged('post_install', '-at_install')
class TestSequenceMixin(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls, chart_template_ref=None):
        super().setUpClass(chart_template_ref=chart_template_ref)
        cls.test_move = cls.create_move()

    @classmethod
    def create_move(cls, move_type=None, date=None, journal=None, name=None, post=False):
        move = cls.env['account.move'].create({
            'move_type': move_type or 'entry',
            'date': date or '2016-01-01',
            'line_ids': [
                (0, None, {
                    'name': 'line',
                    'account_id': cls.company_data['default_account_revenue'].id,
                }),
            ]
        })
        if journal:
            move.name = False
            move.journal_id = journal
        if name:
            move.name = name
        if post:
            move.action_post()
        return move

    def test_sequence_change_date(self):
        """Change the sequence when we change the date iff it has never been posted."""
        # Check setup
        self.assertEqual(self.test_move.state, 'draft')
        self.assertEqual(self.test_move.name, 'MISC/2016/01/0001')
        self.assertEqual(fields.Date.to_string(self.test_move.date), '2016-01-01')

        # Never posetd, the number must change if we change the date
        self.test_move.date = '2020-02-02'
        self.assertEqual(self.test_move.name, 'MISC/2020/02/0001')

        # We don't recompute user's input when posting
        self.test_move.name = 'MyMISC/2020/0000001'
        self.test_move.action_post()
        self.assertEqual(self.test_move.name, 'MyMISC/2020/0000001')

        # Has been posted, and it doesn't change anymore
        self.test_move.button_draft()
        self.test_move.date = '2020-01-02'
        self.test_move.action_post()
        self.assertEqual(self.test_move.name, 'MyMISC/2020/0000001')


    def test_sequence_draft_change_date(self):
        # When a draft entry is added to an empty period, it should get a name.
        # When a draft entry with a name is moved to a period already having entries, its name should be reset to '/'.

        new_move = self.test_move.copy({'date': '2016-02-01'})
        new_multiple_move_1 = self.test_move.copy({'date': '2016-03-01'})
        new_multiple_move_2 = self.test_move.copy({'date': '2016-04-01'})
        new_moves = new_multiple_move_1 + new_multiple_move_2

        # Empty period, so a name should be set
        self.assertEqual(new_move.name, 'MISC/2016/02/0001')
        self.assertEqual(new_multiple_move_1.name, 'MISC/2016/03/0001')
        self.assertEqual(new_multiple_move_2.name, 'MISC/2016/04/0001')

        # Move to an existing period with another move in it
        new_move.date = fields.Date.to_date('2016-01-10')
        new_moves.date = fields.Date.to_date('2016-01-15')

        # Not an empty period, so names should be reset to '/' (draft)
        self.assertEqual(new_move.name, '/')
        self.assertEqual(new_multiple_move_1.name, '/')
        self.assertEqual(new_multiple_move_2.name, '/')

        # Move back to a period with no moves in it
        new_move.date = fields.Date.to_date('2016-02-01')
        new_moves.date = fields.Date.to_date('2016-03-01')

        # All moves in the previously empty periods should be given a name instead of `/`
        self.assertEqual(new_move.name, 'MISC/2016/02/0001')
        self.assertEqual(new_multiple_move_1.name, 'MISC/2016/03/0001')
        # Since this is the second one in the same period, it should remain `/`
        self.assertEqual(new_multiple_move_2.name, '/')

        # Move both moves back to different periods, both with already moves in it.
        new_multiple_move_1.date = fields.Date.to_date('2016-01-10')
        new_multiple_move_2.date = fields.Date.to_date('2016-02-10')

        # Moves are not in empty periods, so names should be set to '/' (draft)
        self.assertEqual(new_multiple_move_1.name, '/')
        self.assertEqual(new_multiple_move_2.name, '/')

        # Change the journal of the last two moves (empty)
        journal = self.env['account.journal'].create({
            'name': 'awesome journal',
            'type': 'general',
            'code': 'AJ',
        })
        new_moves.journal_id = journal

        # Both moves should be assigned a name, since no moves are in the journal and they are in different periods.
        self.assertEqual(new_multiple_move_1.name, 'AJ/2016/01/0001')
        self.assertEqual(new_multiple_move_2.name, 'AJ/2016/02/0001')


    def test_journal_sequence(self):
        self.assertEqual(self.test_move.name, 'MISC/2016/01/0001')
        self.test_move.action_post()
        self.assertEqual(self.test_move.name, 'MISC/2016/01/0001')

        copy1 = self.create_move(date=self.test_move.date)
        self.assertEqual(copy1.name, '/')
        copy1.action_post()
        self.assertEqual(copy1.name, 'MISC/2016/01/0002')

        copy2 = self.create_move(date=self.test_move.date)
        new_journal = self.test_move.journal_id.copy()
        new_journal.code = "MISC2"
        copy2.journal_id = new_journal
        self.assertEqual(copy2.name, 'MISC2/2016/01/0001')
        with Form(copy2) as move_form:  # It is editable in the form
            with mute_logger('flectra.tests.common.onchange'):
                move_form.name = 'MyMISC/2016/0001'
                self.assertIn(
                    'The sequence will restart at 1 at the start of every year',
                    move_form._perform_onchange(['name'])['warning']['message'],
                )
            move_form.journal_id = self.test_move.journal_id
            self.assertEqual(move_form.name, '/')
            move_form.journal_id = new_journal
            self.assertEqual(move_form.name, 'MISC2/2016/01/0001')
            with mute_logger('flectra.tests.common.onchange'):
                move_form.name = 'MyMISC/2016/0001'
                self.assertIn(
                    'The sequence will restart at 1 at the start of every year',
                    move_form._perform_onchange(['name'])['warning']['message'],
                )
        copy2.action_post()
        self.assertEqual(copy2.name, 'MyMISC/2016/0001')

        copy3 = self.create_move(date=copy2.date, journal=new_journal)
        self.assertEqual(copy3.name, '/')
        with self.assertRaises(AssertionError):
            with Form(copy2) as move_form:  # It is not editable in the form
                move_form.name = 'MyMISC/2016/0002'
        copy3.action_post()
        self.assertEqual(copy3.name, 'MyMISC/2016/0002')
        copy3.name = 'MISC2/2016/00002'

        copy4 = self.create_move(date=copy2.date, journal=new_journal)
        copy4.action_post()
        self.assertEqual(copy4.name, 'MISC2/2016/00003')

        copy5 = self.create_move(date=copy2.date, journal=new_journal)
        copy5.date = '2021-02-02'
        copy5.action_post()
        self.assertEqual(copy5.name, 'MISC2/2021/00001')
        copy5.name = 'N\'importe quoi?'

        copy6 = self.create_move(date=copy5.date, journal=new_journal)
        copy6.action_post()
        self.assertEqual(copy6.name, 'N\'importe quoi?1')

    def test_journal_sequence_format(self):
        """Test different format of sequences and what it becomes on another period"""
        sequences = [
            ('JRNL/2016/00001', 'JRNL/2016/00002', 'JRNL/2016/00003', 'JRNL/2017/00001'),
            ('1234567', '1234568', '1234569', '1234570'),
            ('20190910', '20190911', '20190912', '20190913'),
            ('2016-0910', '2016-0911', '2016-0912', '2017-0001'),
            ('201603-10', '201603-11', '201604-01', '201703-01'),
            ('16-03-10', '16-03-11', '16-04-01', '17-03-01'),
            ('2016-10', '2016-11', '2016-12', '2017-01'),
            ('045-001-000002', '045-001-000003', '045-001-000004', '045-001-000005'),
            ('JRNL/2016/00001suffix', 'JRNL/2016/00002suffix', 'JRNL/2016/00003suffix', 'JRNL/2017/00001suffix'),
        ]

        init_move = self.create_move(date='2016-03-12')
        next_move = self.create_move(date='2016-03-12')
        next_move_month = self.create_move(date='2016-04-12')
        next_move_year = self.create_move(date='2017-03-12')
        next_moves = (next_move + next_move_month + next_move_year)
        next_moves.action_post()

        for sequence_init, sequence_next, sequence_next_month, sequence_next_year in sequences:
            init_move.name = sequence_init
            next_moves.name = False
            next_moves._compute_name()
            self.assertEqual(
                [next_move.name, next_move_month.name, next_move_year.name],
                [sequence_next, sequence_next_month, sequence_next_year],
            )

    def test_journal_next_sequence(self):
        """Sequences behave correctly even when there is not enough padding."""
        prefix = "TEST_ORDER/2016/"
        self.test_move.name = f"{prefix}1"
        for c in range(2, 25):
            copy = self.create_move(date=self.test_move.date)
            copy.name = "/"
            copy.action_post()
            self.assertEqual(copy.name, f"{prefix}{c}")

    def test_journal_sequence_multiple_type(self):
        """Domain is computed accordingly to different types."""
        entry, entry2, invoice, invoice2, refund, refund2 = (
            self.create_move(date='2016-01-01')
            for i in range(6)
        )
        (invoice + invoice2 + refund + refund2).write({
            'journal_id': self.company_data['default_journal_sale'].id,
            'partner_id': 1,
            'invoice_date': '2016-01-01',
        })
        (invoice + invoice2).move_type = 'out_invoice'
        (refund + refund2).move_type = 'out_refund'
        all = (entry + entry2 + invoice + invoice2 + refund + refund2)
        all.name = False
        all.action_post()
        self.assertEqual(entry.name, 'MISC/2016/01/0002')
        self.assertEqual(entry2.name, 'MISC/2016/01/0003')
        self.assertEqual(invoice.name, 'INV/2016/01/0001')
        self.assertEqual(invoice2.name, 'INV/2016/01/0002')
        self.assertEqual(refund.name, 'RINV/2016/01/0001')
        self.assertEqual(refund2.name, 'RINV/2016/01/0002')

    def test_journal_sequence_groupby_compute(self):
        """The grouping optimization is correctly done."""
        # Setup two journals with a sequence that resets yearly
        journals = self.env['account.journal'].create([{
            'name': f'Journal{i}',
            'code': f'J{i}',
            'type': 'general',
        } for i in range(2)])
        account = self.env['account.account'].search([], limit=1)
        moves = self.env['account.move'].create([{
            'journal_id': journals[i].id,
            'line_ids': [(0, 0, {'account_id': account.id, 'name': 'line'})],
            'date': '2010-01-01',
        } for i in range(2)])._post()
        for i in range(2):
            moves[i].name = f'J{i}/2010/00001'

        # Check that the moves are correctly batched
        moves = self.env['account.move'].create([{
            'journal_id': journals[journal_index].id,
            'line_ids': [(0, 0, {'account_id': account.id, 'name': 'line'})],
            'date': f'2010-{month}-01',
        } for journal_index, month in [(1, 1), (0, 1), (1, 2), (1, 1)]])._post()
        self.assertEqual(
            moves.mapped('name'),
            ['J1/2010/00002', 'J0/2010/00002', 'J1/2010/00004', 'J1/2010/00003'],
        )

        journals[0].code = 'OLD'
        journals.flush()
        journal_same_code = self.env['account.journal'].create([{
            'name': 'Journal0',
            'code': 'J0',
            'type': 'general',
        }])
        moves = (
            self.create_move(date='2010-01-01', journal=journal_same_code, name='J0/2010/00001')
            + self.create_move(date='2010-01-01', journal=journal_same_code)
            + self.create_move(date='2010-01-01', journal=journal_same_code)
            + self.create_move(date='2010-01-01', journal=journals[0])
        )._post()
        self.assertEqual(
            moves.mapped('name'),
            ['J0/2010/00001', 'J0/2010/00002', 'J0/2010/00003', 'J0/2010/00003'],
        )

    def test_journal_override_sequence_regex(self):
        """There is a possibility to override the regex and change the order of the paramters."""
        self.create_move(date='2020-01-01', name='00000876-G 0002/2020')
        next = self.create_move(date='2020-01-01')
        next.action_post()
        self.assertEqual(next.name, '00000876-G 0002/2021')  # Wait, I didn't want this!

        next.button_draft()
        next.name = False
        next.journal_id.sequence_override_regex = r'^(?P<seq>\d*)(?P<suffix1>.*?)(?P<year>(\d{4})?)(?P<suffix2>)$'
        next.action_post()
        self.assertEqual(next.name, '00000877-G 0002/2020')  # Pfew, better!
        next = self.create_move(date='2020-01-01')
        next.action_post()
        self.assertEqual(next.name, '00000878-G 0002/2020')

        next = self.create_move(date='2017-05-02')
        next.action_post()
        self.assertEqual(next.name, '00000001-G 0002/2017')

    def test_journal_sequence_ordering(self):
        """Entries are correctly sorted when posting multiple at once."""
        self.test_move.name = 'XMISC/2016/00001'
        copies = reduce((lambda x, y: x+y), [
            self.create_move(date=self.test_move.date)
            for i in range(6)
        ])

        copies[0].date = '2019-03-05'
        copies[1].date = '2019-03-06'
        copies[2].date = '2019-03-07'
        copies[3].date = '2019-03-04'
        copies[4].date = '2019-03-05'
        copies[5].date = '2019-03-05'
        # that entry is actualy the first one of the period, so it already has a name
        # set it to '/' so that it is recomputed at post to be ordered correctly.
        copies[0].name = '/'
        copies.action_post()

        # Ordered by date
        self.assertEqual(copies[0].name, 'XMISC/2019/00002')
        self.assertEqual(copies[1].name, 'XMISC/2019/00005')
        self.assertEqual(copies[2].name, 'XMISC/2019/00006')
        self.assertEqual(copies[3].name, 'XMISC/2019/00001')
        self.assertEqual(copies[4].name, 'XMISC/2019/00003')
        self.assertEqual(copies[5].name, 'XMISC/2019/00004')

        # Can't have twice the same name
        with self.assertRaises(ValidationError):
            copies[0].name = 'XMISC/2019/00001'

        # Lets remove the order by date
        copies[0].name = 'XMISC/2019/10001'
        copies[1].name = 'XMISC/2019/10002'
        copies[2].name = 'XMISC/2019/10003'
        copies[3].name = 'XMISC/2019/10004'
        copies[4].name = 'XMISC/2019/10005'
        copies[5].name = 'XMISC/2019/10006'

        copies[4].button_draft()
        copies[4].with_context(force_delete=True).unlink()
        copies[5].button_draft()

        wizard = Form(self.env['account.resequence.wizard'].with_context(
            active_ids=set(copies.ids) - set(copies[4].ids),
            active_model='account.move'),
        )

        new_values = json.loads(wizard.new_values)
        self.assertEqual(new_values[str(copies[0].id)]['new_by_date'], 'XMISC/2019/10002')
        self.assertEqual(new_values[str(copies[0].id)]['new_by_name'], 'XMISC/2019/10001')

        self.assertEqual(new_values[str(copies[1].id)]['new_by_date'], 'XMISC/2019/10004')
        self.assertEqual(new_values[str(copies[1].id)]['new_by_name'], 'XMISC/2019/10002')

        self.assertEqual(new_values[str(copies[2].id)]['new_by_date'], 'XMISC/2019/10005')
        self.assertEqual(new_values[str(copies[2].id)]['new_by_name'], 'XMISC/2019/10003')

        self.assertEqual(new_values[str(copies[3].id)]['new_by_date'], 'XMISC/2019/10001')
        self.assertEqual(new_values[str(copies[3].id)]['new_by_name'], 'XMISC/2019/10004')

        self.assertEqual(new_values[str(copies[5].id)]['new_by_date'], 'XMISC/2019/10003')
        self.assertEqual(new_values[str(copies[5].id)]['new_by_name'], 'XMISC/2019/10005')

        wizard.save().resequence()

        self.assertEqual(copies[3].state, 'posted')
        self.assertEqual(copies[5].name, 'XMISC/2019/10005')
        self.assertEqual(copies[5].state, 'draft')

    def test_sequence_get_more_specific(self):
        """There is the ability to change the format (i.e. from yearly to montlhy)."""
        def test_date(date, name):
            test = self.create_move(date=date)
            test.action_post()
            self.assertEqual(test.name, name)

        def set_sequence(date, name):
            return self.create_move(date=date, name=name)._post()

        # Start with a continuous sequence
        self.test_move.name = 'MISC/00001'

        # Change the prefix to reset every year starting in 2017
        new_year = set_sequence(self.test_move.date + relativedelta(years=1), 'MISC/2017/00001')

        # Change the prefix to reset every month starting in February 2017
        new_month = set_sequence(new_year.date + relativedelta(months=1), 'MISC/2017/02/00001')

        test_date(self.test_move.date, 'MISC/00002')  # Keep the old prefix in 2016
        test_date(new_year.date, 'MISC/2017/00002')  # Keep the new prefix in 2017
        test_date(new_month.date, 'MISC/2017/02/00002')  # Keep the new prefix in February 2017

        # Change the prefix to never reset (again) year starting in 2018 (Please don't do that)
        reset_never = set_sequence(self.test_move.date + relativedelta(years=2), 'MISC/00100')
        test_date(reset_never.date, 'MISC/00101')  # Keep the new prefix in 2018

    def test_resequence_clash(self):
        """Resequence doesn't clash when it uses a name set in the same batch
        but that will be overriden later."""
        moves = self.env['account.move']
        for i in range(3):
            moves += self.create_move(name=str(i))
        moves.action_post()

        mistake = moves[1]
        mistake.button_draft()
        mistake.posted_before = False
        mistake.unlink()
        moves -= mistake

        self.env['account.resequence.wizard'].create({
            'move_ids': moves.ids,
            'first_name': '2',
        }).resequence()

    @freeze_time('2021-10-01 00:00:00')
    def test_change_journal_on_first_account_move(self):
        """Changing the journal on the first move is allowed"""
        journal = self.env['account.journal'].create({
            'name': 'awesome journal',
            'type': 'general',
            'code': 'AJ',
        })
        move = self.env['account.move'].create({})
        self.assertEqual(move.name, 'MISC/2021/10/0001')
        with Form(move) as move_form:
            move_form.journal_id = journal
        self.assertEqual(move.name, 'AJ/2021/10/0001')


@tagged('post_install', '-at_install')
class TestSequenceMixinConcurrency(TransactionCase):
    def setUp(self):
        super().setUp()
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            journal = env['account.journal'].create({
                'name': 'concurency_test',
                'code': 'CT',
                'type': 'general',
            })
            account = env['account.account'].create({
                'code': 'CT',
                'name': 'CT',
                'user_type_id': env.ref('account.data_account_type_fixed_assets').id,
            })
            moves = env['account.move'].create([{
                'journal_id': journal.id,
                'date': fields.Date.from_string('2016-01-01'),
                'line_ids': [(0, 0, {'name': 'name', 'account_id': account.id})]
            }] * 3)
            moves.name = '/'
            moves[0].action_post()
            self.assertEqual(moves.mapped('name'), ['CT/2016/01/0001', '/', '/'])
            env.cr.commit()
        self.data = {
            'move_ids': moves.ids,
            'account_id': account.id,
            'journal_id': journal.id,
            'envs': [
                api.Environment(self.env.registry.cursor(), SUPERUSER_ID, {}),
                api.Environment(self.env.registry.cursor(), SUPERUSER_ID, {}),
                api.Environment(self.env.registry.cursor(), SUPERUSER_ID, {}),
            ],
        }
        self.addCleanup(self.cleanUp)

    def cleanUp(self):
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            moves = env['account.move'].browse(self.data['move_ids'])
            moves.button_draft()
            moves.posted_before = False
            moves.unlink()
            journal = env['account.journal'].browse(self.data['journal_id'])
            journal.unlink()
            account = env['account.account'].browse(self.data['account_id'])
            account.unlink()
            env.cr.commit()
        for env in self.data['envs']:
            env.cr.close()

    def test_sequence_concurency(self):
        """Computing the same name in concurent transactions is not allowed."""
        env0, env1, env2 = self.data['envs']

        # start the transactions here on cr1 to simulate concurency with cr2
        env1.cr.execute('SELECT 1')

        # post in cr2
        move = env2['account.move'].browse(self.data['move_ids'][1])
        move.action_post()
        env2.cr.commit()

        # try to post in cr1, should fail because this transaction started before the post in cr2
        move = env1['account.move'].browse(self.data['move_ids'][2])
        with self.assertRaises(psycopg2.OperationalError), mute_logger('flectra.sql_db'):
            move.action_post()

        # check the values
        moves = env0['account.move'].browse(self.data['move_ids'])
        self.assertEqual(moves.mapped('name'), ['CT/2016/01/0001', 'CT/2016/01/0002', '/'])

    def test_sequence_concurency_no_useless_lock(self):
        """Do not lock needlessly when the sequence is not computed"""
        env0, env1, env2 = self.data['envs']

        # start the transactions here on cr1 to simulate concurency with cr2
        env1.cr.execute('SELECT 1')

        # get the last sequence in cr1 (for instance opening a form view)
        move = env2['account.move'].browse(self.data['move_ids'][1])
        move.highest_name
        env2.cr.commit()

        # post in cr1, should work even though cr2 read values
        move = env1['account.move'].browse(self.data['move_ids'][2])
        move.action_post()
        env1.cr.commit()

        # check the values
        moves = env0['account.move'].browse(self.data['move_ids'])
        self.assertEqual(moves.mapped('name'), ['CT/2016/01/0001', '/', 'CT/2016/01/0002'])
