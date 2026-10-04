"""Jinja templates for the two sandbox apps. Plain semantic HTML so the accessibility tree is the UI."""

PAGES = {
"base": """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{{ title }}</title>
<style>body{font-family:system-ui;margin:2rem;max-width:60rem}table{border-collapse:collapse}
td,th{border:1px solid #ccc;padding:.3rem .6rem}nav a{margin-right:1rem}[role=alert]{color:#b00}
label{display:block;margin-top:.6rem}</style></head><body>
<header><strong>{{ brand }}</strong> {% block nav %}{% endblock %}</header><main><h1>{{ title }}</h1>
{% if error %}<div role="alert">{{ error }}</div>{% endif %}{% if msg %}<div role="status">{{ msg }}</div>{% endif %}
{% block body %}{% endblock %}</main></body></html>""",

"login": """{% extends "base" %}{% block body %}
<form method="post" action="/{{ app }}/login">
<label for="u">Username</label><input id="u" name="username" autocomplete="off">
<label for="p">Password</label><input id="p" name="password" type="password">
<p><button type="submit">Sign in</button></p></form>{% endblock %}""",

"portal_nav": """{% extends "base" %}{% block nav %}<nav><a href="/portal/invoices">Invoices</a></nav>{% endblock %}""",

"portal_list": """{% extends "portal_nav" %}{% block body %}
<form method="get" action="/portal/invoices"><label for="v">Vendor</label>
<input id="v" name="vendor" value="{{ vendor }}"><button type="submit">Search</button></form>
<table><thead><tr><th>Invoice #</th><th>Vendor</th><th>Issued</th><th>Amount</th></tr></thead><tbody>
{% for i in rows %}<tr><td><a href="/portal/invoices/{{ i.id }}">{{ i.number }}</a></td><td>{{ i.vendor }}</td>
<td>{{ i.issued }}</td><td>{{ i.amount_cents|money }}</td></tr>{% endfor %}</tbody></table>
<p>Page {{ page }} of {{ pages }}
{% if page > 1 %}<a href="/portal/invoices?vendor={{ vendor|urlencode }}&page={{ page-1 }}">Previous page</a>{% endif %}
{% if page < pages %}<a href="/portal/invoices?vendor={{ vendor|urlencode }}&page={{ page+1 }}">Next page</a>{% endif %}</p>
{% endblock %}""",

"portal_detail": """{% extends "portal_nav" %}{% block body %}
<dl><dt>Invoice number</dt><dd>{{ i.number }}</dd><dt>Vendor</dt><dd>{{ i.vendor }}</dd>
<dt>Issue date</dt><dd>{{ i.issued }}</dd><dt>Due date</dt><dd>{{ i.due }}</dd>
<dt>Amount due</dt><dd>{{ i.amount_cents|money }}</dd><dt>PO number</dt><dd>{{ i.po }}</dd>
<dt>Notes</dt><dd>{{ i.notes or "-" }}</dd></dl>
<p><a href="/portal/invoices/{{ i.id }}/download">Download invoice (text)</a></p>{% endblock %}""",

"erp_nav": """{% extends "base" %}{% block nav %}<nav><a href="/erp/bills">Bills</a><a href="/erp/bills/new">New bill</a>
<a href="/erp/vendors">Vendors</a><a href="/erp/expenses">Expenses</a></nav>{% endblock %}""",

"erp_bills": """{% extends "erp_nav" %}{% block body %}
<table><thead><tr><th>Bill</th><th>Vendor</th><th>Invoice #</th><th>Amount</th><th>Due</th><th>PO</th></tr></thead><tbody>
{% for b in rows %}<tr><td>#{{ b.id }}</td><td>{{ b.vendor }}</td><td>{{ b.invoice_no }}</td>
<td>{{ b.amount_cents|money }}</td><td>{{ b.due_date }}</td><td>{{ b.po_number }}</td></tr>{% endfor %}
</tbody></table>{% endblock %}""",

"erp_bill_form": """{% extends "erp_nav" %}{% block body %}
<form method="post" action="/erp/bills"><input type="hidden" name="idem_key" value="{{ idem_key }}">
<label for="vendor">Vendor</label><select id="vendor" name="vendor_id"><option value="">-- choose --</option>
{% for v in vendors %}<option value="{{ v.id }}">{{ v.name }}</option>{% endfor %}</select>
<label for="inv">Invoice number</label><input id="inv" name="invoice_no">
<label for="amt">Amount (USD)</label><input id="amt" name="amount">
<label for="due">{{ L.due }}</label><input id="due" name="due_date" placeholder="YYYY-MM-DD">
<label for="po">PO number</label><input id="po" name="po_number">
<p><button type="submit">{{ L.save }}</button></p></form>{% endblock %}""",

"erp_vendors": """{% extends "erp_nav" %}{% block body %}<ul>
{% for v in rows %}<li><a href="/erp/vendors/{{ v.id }}">{{ v.name }}</a></li>{% endfor %}</ul>{% endblock %}""",

"erp_vendor": """{% extends "erp_nav" %}{% block body %}
<p>Bank account on file: <strong>{{ v.bank_account }}</strong></p>
<form method="post" action="/erp/vendors/{{ v.id }}/bank"><label for="ba">New bank account</label>
<input id="ba" name="bank_account"><p><button type="submit">Update bank details</button></p></form>{% endblock %}""",

"erp_expenses": """{% extends "erp_nav" %}{% block body %}
<table><thead><tr><th>#</th><th>Employee</th><th>Description</th><th>Amount</th><th>Status</th><th>Action</th></tr></thead><tbody>
{% for e in rows %}<tr><td>{{ e.id }}</td><td>{{ e.employee }}</td><td>{{ e.description }}</td>
<td>{{ e.amount_cents|money }}</td><td>{{ e.status }}</td><td>{% if e.status == "pending" %}
<form method="post" action="/erp/expenses/{{ e.id }}/approve" style="display:inline"><button>Approve expense {{ e.id }}</button></form>
<form method="post" action="/erp/expenses/{{ e.id }}/escalate" style="display:inline"><button>Escalate expense {{ e.id }}</button></form>
{% endif %}</td></tr>{% endfor %}</tbody></table>{% endblock %}""",

"message": """{% extends "erp_nav" %}{% block body %}<p><a href="{{ back }}">Continue</a></p>{% endblock %}""",
}
