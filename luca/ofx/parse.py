import re
from decimal import Decimal
from . import types

tag_with_text = re.compile(r'<([^/][^>]*)>\s*([^<\s]+)')  # TODO: space in val

def unescape(text):
    """Replace SGML character escapes in `text` with literal character."""
    return text.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')

def tags(sgml, tag):
    """Return contiguous blocks of text from <tag> to </tag> in `text`."""
    return re.findall('(?s)<{0}>.*?</{0}>'.format(tag), sgml)

def texts(sgml):
    """Return tags and the text inside of them in `sgml`."""
    for tag, text in tag_with_text.findall(sgml):
        yield tag, unescape(text)

def accounts(ofx):
    """Return the list of accounts in the `ofx` text."""
    accounts = []
    for account_type in 'bank', 'cc', 'inv':
        account_tag = '{}ACCTFROM'.format(account_type.upper())
        for sgml in tags(ofx, account_tag):
            attrs = { tag.lower(): text for tag, text in texts(sgml) }
            attrs['type'] = account_type
            attrs['from_element'] = sgml  # save raw SGML, for use in requests
            accounts.append(types.Account(attrs))
    return accounts

def activity(ofx):
    """Return activity."""
    balances = {}
    transactions = {}

    for stmtrs in tags(ofx, 'STMTRS'):
        if 'BANKACCTFROM' not in stmtrs:
            continue

        atexts = dict(texts(tags(ofx, 'BANKACCTFROM')[0]))
        key = (atexts['BANKID'], atexts['ACCTID'], atexts['ACCTTYPE'])

        balance_texts = texts(tags(ofx, 'LEDGERBAL')[0])
        balances[key] = Decimal(dict(balance_texts)['BALAMT'])
        transactions[key] = transaction_list = []

        for stmttrn in tags(stmtrs, 'STMTTRN'):
            attrs = { tag.lower(): text for tag, text in texts(stmttrn) }
            transaction = types.Transaction(attrs)
            transaction_list.append(transaction)

    return balances, transactions
