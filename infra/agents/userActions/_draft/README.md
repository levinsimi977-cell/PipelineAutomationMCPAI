# User Actions — Draft

Temporary side folder for event simulation (Appium) tooling.

## Files

- `sdk_agent_event_wiring.rules.md` — minimal SDK Agent UI wiring rules
- `discover_events.py` — AuditRecord → `events.discovered.json`
- `appium_runner.py` — taps `triggerId` values from discovered JSON

## Manual flow (after team merge)

```bash
python discover_events.py --platform android --audit path/to/audit-record.json
python appium_runner.py --platform android --config events.discovered.json
```

Audit input must follow the existing `AuditRecord` schema (`infra/user_interface_use_case/reports/audit-record.schema.json`).
