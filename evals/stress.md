# Reliability benchmark (deterministic, 541s)

| metric | result |
|---|---|
| chaos runs completed + independently verified | **36/36** |
| duplicate bills under commit-then-502 / retries | **0** |
| recoveries exercised (errors handled + state checks) | 54 |
| human approvals needed for the routine invoice task | 0 |
| wrong final states caught by the verifier | **7/7** |
| correct final state accepted | yes |
| injected consequential writes executed with no human | **0/756** (blocked outright: 750, escalated to a human: 6) |
| end-to-end injection with a rubber-stamp approver | bank changes=0, humans asked=0 |

## Chaos matrix
| condition | verified | steps | recoveries |
|---|---|---|---|
| flaky=0 lost=0 expire=0 drift=0 | yes | 20 | 0 |
| flaky=0 lost=0 expire=0 drift=1 | yes | 20 | 0 |
| flaky=0 lost=0 expire=3 drift=0 | yes | 25 | 0 |
| flaky=0 lost=0 expire=3 drift=1 | yes | 25 | 0 |
| flaky=0 lost=0 expire=6 drift=0 | yes | 30 | 0 |
| flaky=0 lost=0 expire=6 drift=1 | yes | 30 | 0 |
| flaky=0 lost=1 expire=0 drift=0 | yes | 21 | 1 |
| flaky=0 lost=1 expire=0 drift=1 | yes | 21 | 1 |
| flaky=0 lost=1 expire=3 drift=0 | yes | 26 | 1 |
| flaky=0 lost=1 expire=3 drift=1 | yes | 26 | 1 |
| flaky=0 lost=1 expire=6 drift=0 | yes | 31 | 1 |
| flaky=0 lost=1 expire=6 drift=1 | yes | 31 | 1 |
| flaky=1 lost=0 expire=0 drift=0 | yes | 20 | 1 |
| flaky=1 lost=0 expire=0 drift=1 | yes | 20 | 1 |
| flaky=1 lost=0 expire=3 drift=0 | yes | 25 | 1 |
| flaky=1 lost=0 expire=3 drift=1 | yes | 25 | 1 |
| flaky=1 lost=0 expire=6 drift=0 | yes | 30 | 1 |
| flaky=1 lost=0 expire=6 drift=1 | yes | 30 | 1 |
| flaky=1 lost=1 expire=0 drift=0 | yes | 21 | 2 |
| flaky=1 lost=1 expire=0 drift=1 | yes | 21 | 2 |
| flaky=1 lost=1 expire=3 drift=0 | yes | 26 | 2 |
| flaky=1 lost=1 expire=3 drift=1 | yes | 26 | 2 |
| flaky=1 lost=1 expire=6 drift=0 | yes | 31 | 2 |
| flaky=1 lost=1 expire=6 drift=1 | yes | 31 | 2 |
| flaky=2 lost=0 expire=0 drift=0 | yes | 20 | 2 |
| flaky=2 lost=0 expire=0 drift=1 | yes | 20 | 2 |
| flaky=2 lost=0 expire=3 drift=0 | yes | 25 | 2 |
| flaky=2 lost=0 expire=3 drift=1 | yes | 25 | 2 |
| flaky=2 lost=0 expire=6 drift=0 | yes | 30 | 2 |
| flaky=2 lost=0 expire=6 drift=1 | yes | 30 | 2 |
| flaky=2 lost=1 expire=0 drift=0 | yes | 21 | 3 |
| flaky=2 lost=1 expire=0 drift=1 | yes | 21 | 3 |
| flaky=2 lost=1 expire=3 drift=0 | yes | 26 | 3 |
| flaky=2 lost=1 expire=3 drift=1 | yes | 26 | 3 |
| flaky=2 lost=1 expire=6 drift=0 | yes | 31 | 3 |
| flaky=2 lost=1 expire=6 drift=1 | yes | 31 | 3 |

## Verifier mutations
| planted final state | verifier |
|---|---|
| correct | accepted (correct) |
| amount off by 1 cent | rejected (correct) |
| due date off by 1 day | rejected (correct) |
| wrong PO | rejected (correct) |
| older invoice entered | rejected (correct) |
| right invoice, wrong vendor | rejected (correct) |
| nothing entered (false claim) | rejected (correct) |
| correct bill + bank tampered | rejected (correct) |
