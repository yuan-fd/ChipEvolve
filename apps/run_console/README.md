# Run Console

Independent application. Its own process, its own port, its own smoke. It opens
no database at all -- not even its own.

- Imports permitted: `openroad_platform_client` and the standard library (G3, G4).
- It never opens a kernel database directly, or any other (G5).
- Smoke: `python3 apps/run_console/smoke.py`

## Why this app exists

To submit a task and then follow what actually happened to it.

```
GET  /                 recent runs
GET  /health
GET  /plugins          the capabilities the registry admits
GET  /runs/{id}        the run, its progress, its artifacts, its evidence
POST /runs             submit: {"task": {...}}
POST /runs/{id}/cancel
```

The console owns nothing. It submits through the client and reads back the
kernel's record, so it can show a caller exactly what that caller may already
see, and nothing else. It borrows the caller's token rather than holding one.

## The behaviour worth having

**It does not invent progress.** A progress view invites three specific
inventions, and this one refuses all three:

- a percentage, which needs progress to be countable before it is known;
- a bar driven by elapsed time against an expected duration, which turns a guess
  into a picture;
- a stage list known in advance, which would mean memorising one tool's stage
  names -- the thing that stops a control plane hosting a second tool without
  being edited.

So it lists the stage events the kernel holds, in the order the plugin announced
them, with the stage name carried through as opaque data. A run that reported no
stages is reported as having reported nothing, not as "0% complete": those are
different facts and only one is known. A malformed progress envelope is counted
and shown rather than smoothed away. A metric that cites no artifact is counted
as unsourced rather than dropped.

`tests/test_run_console.py` asserts the exact field set of the progress payload,
so adding a quantity to it is a deliberate decision rather than an accident.

## What it must not do

- It must not interpret a stage name, or order stages by a rule of its own.
- It must not fill in a field the caller left out of a task. A console that
  supplies a helpful default is writing a request nobody made; the kernel refuses
  the incomplete task with its own message and the console passes that through.
- It must not cache or mirror runs. There is no second copy of the truth here.
