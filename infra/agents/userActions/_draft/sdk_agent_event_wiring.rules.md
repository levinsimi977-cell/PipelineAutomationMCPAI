# SDK Agent — In-App Event UI Wiring Rules

These rules apply when the SDK Agent creates in-app events via MCP tools (`createInAppEvent`, etc.).
They do not restrict which MCP tools to call or which events to choose — only how each created event must be wired for later Appium discovery.

## Absolute prohibition

It is forbidden to create an in-app event in the application without associating a `triggerId` in the UI.

## Naming convention

Only official AppsFlyer event names are allowed (must start with `af_`).

## triggerId format

The format must always be:

```
af_trigger_{eventName}
```

Example: `af_trigger_af_purchase`

## Android XML

Every wired control must include this attribute on the button/view:

```xml
android:contentDescription="af_trigger_{eventName}"
```

## iOS UI

Every wired control must include:

```
accessibilityIdentifier="af_trigger_{eventName}"
```

## Audit registration

For every created event, the pipeline audit (existing `AuditRecord` schema) must record:

- `eventName`
- `triggerId`
- layout/view file (when known)

Record these inside the audit event `details` field (JSON recommended).
