# -*- coding: utf-8 -*-
"""Tally expenses by category, driven by bank statements and YAML rules."""

import csv
import os
import re
import sys
from StringIO import StringIO
from collections import defaultdict
from copy import copy
from decimal import Decimal
from itertools import groupby
from operator import attrgetter
from textwrap import wrap

import yaml
from .fix_PyYAML import Loader

from luca.importer.dccu import import_dccu_checking_pdf, import_dccu_visa_pdf
from luca.importer import autocsv
from luca.kit import cents
from luca.pdf import extract_text_from_pdf_file
from luca import rules

importers = [import_dccu_checking_pdf, import_dccu_visa_pdf]

def sum_categories(transactions):
    sums = defaultdict(Decimal)
    for tr in transactions:
        for a in category_and_ancestors('Accounts.' + tr.account):
            sums[a] += tr.amount
        for c in category_and_ancestors(tr.category):
            sums[c] -= tr.amount
    return sums

def category_and_ancestors(category):
    """Generate a list of strings: a category and all its ancestors.

    >>> for c in category_and_ancestors('Expenses.Travel.PyCon'):
    ...     c
    ...
    'Expenses.Travel.PyCon'
    'Expenses.Travel'
    'Expenses'

    """
    i = category.rfind('.')
    while i != -1:
        yield category
        category = category[:i]
        i = category.rfind('.')
    yield category


def _transaction_key(tr):
    return tr.category, tr.date, tr.description, tr.amount

def group_transactions_by_category(transactions):
    """Return a new list [(category, [transaction, ...], ...]."""
    tlist = sorted(transactions, key=_transaction_key)
    return {category: list(iterator) for category, iterator
            in groupby(tlist, lambda tr: tr.category)}


def run_yaml_file(terminal, path, statement_paths,
                  show_balances, show_data, show_transactions,
                  sort_by='date'):

    t = terminal
    screen_width = int(os.environ.get('COLUMNS', '0')) or t.width or 80

    with open(path) as yaml_file:
        rule_tree = yaml.load(yaml_file, Loader=Loader)

    try:
        rule_tree_function = rules.compile_tree(rule_tree)
    except rules.ParseError as e:
        sys.stderr.write('ERROR: {}\n'.format(e))
        sys.exit(1)

    balances = []
    transactions = []

    for path in statement_paths:
        if path.lower().endswith('.pdf'):
            text = extract_text_from_pdf_file(path)

        elif path.lower().endswith('.txt'):
            with open(path) as text_file:
                text = text_file.read().decode('utf-8')

            matching_importers = [importer for importer in importers
                                  if importer.does_this_match(text)]

            if len(matching_importers) == 0:
                raise RuntimeError('cannot find an importer for file: {}'
                                   .format(path))
            elif len(matching_importers) > 1:
                raise RuntimeError('found too many importers for file: {}'
                                   .format(path))
            else:
                importer = matching_importers[0]

            new_balances, new_transactions = importer(text)

        elif path.lower().endswith('.csv'):
            with open(path) as csv_file:
                new_balances, new_transactions = autocsv.importer(csv_file)

        else:
            raise ValueError('no idea what to do with file {!r}'.format(path))

        balances.extend(new_balances)
        transactions.extend(new_transactions)

    output_lines = []
    add = output_lines.append

    for line in verify_balances(balances, transactions, show_balances, t):
        add(line)

    new_splits = []

    for tr in transactions:
        tr.set_full_text()
        category = rule_tree_function(tr)
        if category is not None and category.startswith('Split '):
            pieces = [piece.strip() for piece in category[6:].split(';')]
            full_amount = tr.amount
            for piece in pieces[:-1]:
                tr2 = copy(tr)
                #tr2.category, amount_str = piece.split()
                tr2.category, amount_str = piece.rsplit(None, 1)
                if amount_str.endswith('%'):
                    percent = Decimal(amount_str[:-1])
                    tr2.amount = cents(full_amount * percent / 100)
                else:
                    tr2.amount = Decimal(amount_str.replace(',', ''))
                new_splits.append(tr2)
                tr.amount -= tr2.amount
            category = pieces[-1]
        tr.category = category

    transactions.extend(new_splits)
    transactions = [tr for tr in transactions if tr.category is not None]

    if show_data:
        sio = StringIO()
        w = csv.writer(sio)
        w.writerow(['date', 'amount', 'category', 'account', 'description'])
        for t in transactions:
            w.writerow([
                t.date, t.amount, t.category, t.account, t.description,
                ])
        return sio.getvalue().splitlines()

    catdict = group_transactions_by_category(transactions)
    sumdict = sum_categories(transactions)
    categories = set(catdict) | set(sumdict)

    max_amount = max(abs(v) for v in sumdict.values())
    amount_width = len('{:,}'.format(max_amount)) + 1

    date_width = 10
    gutter = 1
    category_indent = ' ' * (amount_width + 1)
    description_indent = ' ' * (gutter + date_width + gutter)

    f = _make_formatter(terminal, amount_width)

    for category in sorted(categories, key=_category_key):
        csum = sumdict[category]
        depth = category.count('.')
        if not show_transactions and depth:
            name = category[category.rfind('.')+1:]
        else:
            name = category
        if show_transactions:
            name = t.bold(name)
        add('{}{} {}'.format(category_indent * depth, f(csum), name))
        if not show_transactions:
            continue
        transaction_list = catdict.get(category, None)
        if not transaction_list:
            continue

        if sort_by == 'amount':
            transaction_list.sort(key=lambda t: -t.amount)
        elif sort_by == 'date':
            transaction_list.sort(key=lambda t: t.date)

        add('')

        max_amount2 = max(abs(tr.amount) for tr in transaction_list)
        amount_width2 = max(10, len('{:,}'.format(max_amount2)) + 1)
        f2 = _make_formatter(terminal, amount_width2)

        description_width = (
            screen_width -  # content from left to right:
            gutter - date_width - gutter - # (description goes here)
            gutter - amount_width2 - gutter)

        for tr in transaction_list:
            lines = wrap(tr.description, description_width)
            add(' {} {:<{}} {:>{}}'.format(
                tr.date, lines[0], description_width,
                f2(tr.amount), amount_width2))
            for line in lines[1:]:
                add(description_indent + line)
        add('')

    return output_lines


_category_key_re = re.compile(r'(\d+)')

def _category_key(category):
    """Return a sort key for `category`.

    >>> _category_key('abc123def456')
    ['abc', 123, 'def', 456, '']

    """
    pieces = _category_key_re.split(category)
    for i in range(1, len(pieces), 2):
        pieces[i] = int(pieces[i])
    return pieces


def _make_formatter(terminal, width):
    """Format `amount`, putting parentheses around a negative value.

    Note that we have to apply `width` before passing the string to
    t.red() and t.green(), since otherwise any ANSI escape characters
    will make the string length incorrect for formatting purposes.

    """
    def format(amount):
        if amount < 0:
            return terminal.red('{:{},}-'.format(-amount, width - 1))
        else:
            return terminal.green('{:{},} '.format(amount, width - 1))

    return format


def verify_balances(balances, transactions, show_balances, t):
    """Raise an exception if the transactions do not match the balances."""

    # We are careful to balances before transactions in the original
    # list so that the stable sort leaves all balances for a particular
    # date in front of any transactions for that same date.

    events = list(balances)
    events.extend(transactions)
    events.sort(key=attrgetter('sort_key'))
    events.sort(key=attrgetter('date'))
    if show_balances:
        events.sort(key=attrgetter('account'))

    amounts = {}

    for e in events:

        if e.event_type == 'balance':
            if e.account not in amounts:
                if show_balances:
                    yield ''
                    yield '{} opening balance {} on {}'.format(
                        e.account, e.amount, e.date)
                    yield ''
                amounts[e.account] = e.amount
                continue

            amount = amounts[e.account]
            equal = (amount == e.amount)

            if not equal:
                yield t.red('{} actual balance {} != statement balance {}'
                            .format(e.date, amount, e.amount))
            elif show_balances:
                yield t.green('{} balance == {}'.format(e.date, amount))

            #assert equal

        elif e.event_type == 'transaction':
            if e.account not in amounts:
                continue

            amount = e.amount
            amounts[e.account] += amount

            if show_balances:
                yield '{} {:+} = {}'.format(e.date, amount, amounts[e.account])

    if show_balances:
        yield ''
