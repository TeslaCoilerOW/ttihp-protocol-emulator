| Case | Expected | Verdict | Captures (records) | Frames identical | Jitter p-p (cycles) | Scope edges = VCD edges | Host operations (burst) | Load evidence in the windows: uio0 / uio2 / ui4 edges |
|---|---|---|---|---|---|---|---|---|
| scope-idle-base-1 | same | same | 2 (2048+2048) | 94/94 | 0 | yes (5264 edges) | 0 (0) | 0 / 0 / 0 |
| scope-idle-host-fetch-1 | different | different | 1 (4) | 0/0 | - | yes (0 edges) | 0 (0) | 0 / 0 / 0 |
| scope-idle-timer-host-1 | same | same | 2 (2048+2048) | 94/94 | 0 | yes (5264 edges) | 0 (0) | 0 / 0 / 0 |
| scope-loaded-base-1 | same | same | 2 (2048+2048) | 95/95 | 0 | yes (5263 edges) | 32 (24) | 289 / 833 / 434 |
| scope-loaded-base-2 | same | same | 2 (2048+2048) | 94/94 | 0 | yes (5268 edges) | 32 (24) | 290 / 832 / 454 |
| scope-loaded-engine-fetch-1 | different | different | 1 (4) | 0/0 | - | yes (0 edges) | 1918 (12) | 52775 / 149023 / 66952 |
| scope-loaded-host-fetch-1 | different | different | 1 (4) | 0/0 | - | yes (0 edges) | 1918 (12) | 52775 / 149023 / 66952 |
| scope-loaded-timer-engine-1 | different | different | 2 (2048+2048) | 12/95 | 319 | yes (5262 edges) | 32 (24) | 411 / 1173 / 596 |
| scope-loaded-timer-host-1 | different | different | 2 (2048+2048) | 85/95 | 8 | yes (5264 edges) | 32 (24) | 290 / 836 / 450 |

ALL PASS
