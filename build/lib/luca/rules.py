"""Compile YAML rules into a Python function.

The `compile_tree()` routine is the main function in this module; the
other functions exist to support it.  See the documentation for more
information about writing YAML rules.

"""
import datetime
import re
from ast import (AST, If, Name, Return, Str, Param, FunctionDef, Interactive,
                 arguments, fix_missing_locations, parse)

_year_month_re = re.compile(u'\d\d\d\d-\d\d$')
_month_day_re = re.compile(u'\d\d/\d\d$')
_month_day_to_month_day_re = re.compile(u'\d\d/\d\d-\d\d/\d\d$')

class ParseError(Exception):
    """Luca cannot understand your YAML."""


def compile_tree(tree):
    tree_node = analyze_tree(tree, None)

    node = Interactive(body=[
        FunctionDef(
            name='run_rules',
            args=arguments(args=[Name(id='t', ctx=Param())],
                           vararg=None, kwarg=None, defaults=[]),
            body=[tree_node],
            decorator_list=[]),
        ])
    fixed = fix_missing_locations(node)
    code = compile(fixed, '<luca rule compiler>', 'single')

    globals_dict = {'datetime': datetime, 'search': re.search}
    eval(code, globals_dict)
    run_rules = globals_dict['run_rules']
    return run_rules


def eparse(source):
    """Parse the expression in `source` and return its AST.

    This function is careful to remove the ast.Expression object which
    ast.parse() wraps around its return value in eval mode.

    """
    expression_node = parse(source, mode='eval')
    return expression_node.body


def analyze_tree(tree, category):

    if isinstance(tree, list):
        subtrees = [analyze_tree(subtree, category) for subtree in tree]
        return If(eparse('True'), subtrees, [])

    elif isinstance(tree, dict):
        if len(tree) != 1:
            raise ValueError('len(dict) != 1')
        rule, subtree = tree.items()[0]
        r = analyze_rule(rule)
        if isinstance(r, AST):
            test = r
            return If(test, [analyze_tree(subtree, category)], [])
        else:
            dueling_category_check(category, r)
            category = r
            return analyze_tree(subtree, category)

    else:
        rule = tree
        r = analyze_rule(rule)
        if isinstance(r, AST):
            test = r
            if category is None:
                raise ValueError('bottomed out without category')
            return If(test, [Return(Str(category))], [])
        else:
            dueling_category_check(category, r)
            category = r
            return Return(Str(category))


def dueling_category_check(old_category, new_category):
    if old_category is not None:
        raise ParseError(
            'dueling categories: within a rule that has already set the'
            ' category to {!r}, you are trying to switch it to {!r}'
            .format(old_category, new_category))


def analyze_rule(rule):
    """Return an AST if `rule` looks like a test, else `rule` itself."""

    if isinstance(rule, datetime.date):
        return eparse('t.date == %r' % (rule,))

    if isinstance(rule, tuple) and len(rule) == 2:
        a, b = rule
        if isinstance(a, datetime.date) and isinstance(b, datetime.date):
            return eparse('t.date >= %r and t.date <= %r' % (a, b))

    if isinstance(rule, str):
        rule = rule.decode('ascii')

    if isinstance(rule, unicode):
        if rule.startswith(u'/') and rule.endswith(u'/'):
            return eparse('search(%r, t.full_text)' % rule[1:-1])
        elif rule.startswith(u'~/') and rule.endswith(u'/'):
            return eparse('not search(%r, t.full_text)' % rule[2:-1])
        else:
            match = _year_month_re.match(rule)
            if match:
                year = int(rule[:4])
                month = int(rule[5:])
                return eparse('t.date.year == %r and t.date.month == %r'
                             % (year, month))

            match = _month_day_re.match(rule)
            if match:
                month = int(rule[:2])
                day = int(rule[3:])
                return eparse('t.date.month == %r and t.date.day == %r'
                             % (month, day))
            else:
                match = _month_day_to_month_day_re.match(rule)
                if match:
                    month1 = int(rule[:2])
                    day1 = int(rule[3:5])
                    month2 = int(rule[6:8])
                    day2 = int(rule[9:11])
                    return eparse(
                        'datetime.date(t.date.year, %r, %r) <= t.date and '
                        't.date <= datetime.date(t.date.year, %r, %r)'
                        % (month1, day1, month2, day2))

    if isinstance(rule, unicode):
        n = int(rule) if rule.isdigit() else None
    else:
        n = rule if isinstance(rule, int) else None

    if (n is not None) and 1900 <= n <= 2100:
        return eparse('t.date.year == %r' % n)
    elif (n is not None) and 1 <= n <= 12:
        return eparse('t.date.month == %r' % n)

    category = rule

    if category is None:
        raise ParseError(
            'your YAML contains a ":" that is not followed by further text'
            ' and which therefore cannot be parsed'.format(category))

    if ' - ' in category:
        raise ParseError(
            'your category name looks suspiciously like malformed YAML'
            ' resulting from a forgotten colon: {}'.format(category))

    return category
