# LS NWS Subscription Acknowledgement Design

- Status: approved operational follow-up
- Date: 2026-07-16
- Parent design: `2026-07-15-ls-nws-readonly-collector-design.md`
- Product boundary: LS read-only news collection only
- Account and order endpoints: forbidden

## 1. Evidence And Root Cause

The first rotated-credential aggregate smoke completed OAuth and opened the allow-listed NWS WebSocket. The first received frame was committed raw-first and the run terminated as `invalid_packet` before any catalyst projection.

Redacted structural inspection of that immutable receipt established this exact shape:

```text
root keys: body, header
body: null
header keys: rsp_cd, rsp_msg, tr_cd, tr_type
tr_cd: NWS
tr_type: subscription type 3
rsp_cd: five-digit success code 00000
```

The current parser accepts only a news data packet with `header.tr_cd`, `header.tr_key` and a non-null NWS body. The failure therefore occurs at the parser boundary: a valid subscription acknowledgement is incorrectly passed to the news parser and classified as an invalid data packet.

The raw frame, source receipt and terminal failed run remain immutable. The failed cycle is not rewritten or resumed; verification after the fix uses a new cycle ID.

## 2. Contract

Add a strict `ParsedLsNwsSubscriptionAck` result alongside `ParsedLsNwsNews`.

A success acknowledgement requires all of the following:

- exact root keys `header`, `body`
- `body is None`
- exact header keys `rsp_cd`, `rsp_msg`, `tr_cd`, `tr_type`
- `tr_cd == "NWS"`
- `tr_type == "3"`
- `rsp_cd == "00000"`
- `rsp_msg` is a trimmed, non-empty, control-character-free string no longer than 200 characters

The acknowledgement result stores no provider message. A control-shaped packet with a non-success code raises `subscription_rejected`; malformed or unknown shapes retain stable sanitized parse codes.

## 3. Collection State Machine

```text
OPEN
-> expect exactly one successful subscription acknowledgement
-> ACKNOWLEDGED
-> accept zero or more strict NWS001 news data frames
-> timeout/frame bound
-> terminal success
```

Rules:

- every control and data frame is appended as a raw source receipt before parsing
- data before acknowledgement fails as `subscription_ack_missing`
- a second acknowledgement fails as `duplicate_subscription_ack`
- a rejected acknowledgement fails as `subscription_rejected`
- a successful acknowledgement advances the expected frame sequence but creates no catalyst or observation
- acknowledgement-only bounded timeout is a successful zero-news run
- a malformed data frame after acknowledgement preserves all prior receipts and catalysts and terminates failed
- `max_frames` counts both control and data frames
- exact terminal restart remains a no-op and never reopens token or network sources

## 4. Result And Reporting

`LsNwsCollectionResult` adds `subscription_acknowledged: bool`. The aggregate report adds only:

```text
subscription acknowledged: yes|no
```

It does not include `rsp_msg`, response code, raw frame, token, endpoint, receipt ID, checksum or provider payload. A failed run continues to expose only its allow-listed failure code.

## 5. Fixtures And Compatibility

The committed NWS fixture begins with one success acknowledgement and then the existing two news frames. Expected receipt count changes from two to three while catalyst and observation counts remain two.

Old data-only fixtures are no longer protocol-complete for the production collection state machine. Pure news parser tests remain valid because the news packet contract itself does not change.

## 6. Tests

- strict success acknowledgement parse without retaining `rsp_msg`
- rejected code, duplicate key, extra field, non-null body and unsafe message rejection
- data-before-ack and duplicate-ack collection failures
- ack-only timeout success with one receipt and zero catalysts
- ack plus two news frames success with three receipts and two catalysts
- malformed data after ack preserves both receipts and terminal failure
- terminal restart performs no opener call
- report contains acknowledgement aggregate and no provider message
- full pytest, Ruff, basedpyright, CLI help, invalid input and fixture happy path
- a second bounded rotated-credential smoke under a new cycle ID

## 7. Completion Criteria

1. The observed success control frame is validated rather than ignored.
2. No news data can become a catalyst before successful subscription acknowledgement.
3. Raw-first, append-only and restart behavior remains intact.
4. The second live smoke reaches acknowledged bounded collection without exposing credentials or provider messages.
5. LS account, balance, position and order calls remain zero.
