Negative fixture for G13.

Two implementations of one concern (a digest) inside the *same* zone.  They must
be in one zone: G13 is applied per independently-authored unit, so a plugin or an
app is allowed its own digest -- it is a separate program and may not be forced
to import the platform's.  What no unit is allowed is two.
