---
mode: agent
description: Audit logging requirements that must be satisfied for every new write endpoint (create/update/delete/state-change). Wires calls into the existing audit + security infrastructure.
---

# Audit Logging Requirements

The audit infrastructure already exists in this repo. **Do not** re-create
tables, helpers, or middleware. **Use** the following:

- Helpers: `app/modules/audit/service.py`
  - `log_action(...)` — general tenant/user actions
  - `log_admin_action(...)` — admin-only actions (writes `admin_action_logs` AND mirrors to `audit_logs`)
- Security helpers: `app/modules/security/`
  - `record_login_attempt`, `ensure_ip_allowed`, `block_ip`, `register_failed_attempt_for_ip`
- Middleware (`app/core/middleware.py`) already populates on every request:
  - `request.state.ip_address`
  - `request.state.user_agent`
  - `request.state.request_path`
  - `request.state.request_id`
- Tables (migration `005_audit_security`):
  `audit_logs`, `admin_action_logs`, `login_attempts`, `ip_blocklist`

## Rules

For **every** create / update / delete / state-change endpoint you build:

1. Inject `request: Request` and the SQLAlchemy `db: Session`.
2. Snapshot the **before** state to a dict (when applicable, e.g. updates/deletes).
3. Mutate the row(s).
4. Snapshot the **after** state to a dict (when applicable, e.g. creates/updates).
5. Call the appropriate helper **before** `db.commit()`. The helpers do NOT commit.

### Tenant / user actions

```python
from app.modules.audit import log_action

log_action(
    db,
    actor_id=current_user.id,
    actor_type="tenant",            # or "user"
    action="<entity>.<verb>",       # e.g. "property.create", "connector.activate"
    entity="<entity>",              # e.g. "property"
    entity_id=row.id,
    before_value=<dict|None>,
    after_value=<dict|None>,
    request=request,                # auto-pulls IP from request.state
)
```

### Admin actions (impersonation, billing change, tenant suspension, data deletion, etc.)

```python
from app.modules.audit import log_admin_action

log_admin_action(
    db,
    admin_id=current_admin.id,
    action="<verb>",                  # e.g. "tenant.suspend"
    target_entity="<entity>",         # e.g. "tenant"
    target_id=row.id,
    target_tenant_id=tenant.id,       # if scoped to a tenant
    before_value=<dict|None>,
    after_value=<dict|None>,
    request=request,
)
# This writes BOTH admin_action_logs AND a mirrored audit_logs row.
```

## Standardised action verbs

Use these names so dashboards/queries stay consistent:

| Module       | Verbs |
|--------------|-------|
| Property     | `property.create`, `property.update`, `property.delete` |
| Connector    | `connector.activate`, `connector.deactivate`, `connector.update` |
| AI drafts    | `ai_draft.approve`, `ai_draft.reject`, `ai_draft.edit` |
| Subscription | `subscription.upgrade`, `subscription.downgrade`, `subscription.cancel`, `subscription.renew` |
| Admin        | `tenant.suspend`, `tenant.delete`, `user.impersonate`, `billing.update`, `data.delete` |

## Logging standards (already enforced by helpers — do NOT bypass)

- `before_value` / `after_value` stored as JSON
- Timestamps are UTC (`timestamptz` with `now()` default)
- `actor_id` is required for non-system events
- Helpers never commit — caller controls the transaction
- Helpers auto-coerce UUIDs and datetimes to JSON-safe values

## Required tests for each new endpoint

Monkeypatch the helper and assert it was called with the expected payload.
Pattern:

```python
def test_property_create_emits_audit(monkeypatch, client, db):
    calls = []
    import app.modules.audit.service as audit_svc
    monkeypatch.setattr(audit_svc, "log_action",
                        lambda db, **kw: calls.append(kw))

    client.post("/api/v1/properties", json={"name": "Acme HQ", ...})

    assert calls, "expected log_action to be called"
    assert calls[0]["action"] == "property.create"
    assert calls[0]["entity"] == "property"
    assert calls[0]["after_value"]["name"] == "Acme HQ"
```

For admin endpoints, monkeypatch `log_admin_action` and assert it received
`admin_id`, `target_entity`, `target_id`, and the before/after payloads.

## Hard rules — do NOT

- Inline raw `INSERT` statements into `audit_logs` / `admin_action_logs`.
- Catch and swallow exceptions raised by the helpers.
- Forget to pass `request=request` (otherwise IP/UA/path are not captured).
- Call `db.commit()` inside the helper or before the helper runs.
- Log secrets (passwords, tokens, full credit-card numbers) in `before/after_value`.

## Acceptance checklist for the PR

- [ ] Every create/update/delete endpoint calls `log_action` or `log_admin_action`
- [ ] All admin-only mutations use `log_admin_action`
- [ ] `request=request` is passed in every call
- [ ] Tests monkeypatch the helper and assert the action verb, entity, and payload
- [ ] No new direct writes to `audit_logs`, `admin_action_logs`, `login_attempts`, or `ip_blocklist`
- [ ] No secrets in `before_value` / `after_value`
