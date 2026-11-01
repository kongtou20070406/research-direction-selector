# Offline model seam

This skeleton contains no Torch dependency, model implementation, weights,
embedding, retrieval provider, trainer, HTTP client or executable loader.
Model identities in the F5i/F7i envelopes are inert id/version/SHA-256 values.
Configured metadata cannot name a Python module, entrypoint or command.

The prediction contract runs in shadow mode with `selection_effect=NONE`.
CPU worker infrastructure is **PARTIAL / NOT IMPLEMENTED**: no process timeout,
resource isolation, output capture or OS sandbox is claimed. A future trusted
L1 worker must use explicit trusted code, enforce bounded input/output and time,
retain actual costs and failure receipts in the existing ledger, and pass pure
values into L3 validation through the composition root. It must not import L3
policy upward or interpret model metadata as executable configuration.

Dataset validation checks declared problem-family separation and retains
before/action/after, decision inputs, forecasts, selections, observations,
versions, failed attempts and unknown costs as distinct fields. It does not
verify source bytes, hidden data exposure or scientific independence.
