| case | job | sim | expected | exact | analyser view | frames identical | host ops | UART bytes |
|---|---|---|---|---|---|---|---|---|
| bridge-idle-base-1 | 23983474 | ok | same | same | same | 1728/1728 | 0 | 0 |
| bridge-idle-host-fetch-1 | 23983480 | ok | different | different | different | 0/0 | 0 | 0 |
| bridge-idle-timer-host-1 | 23983477 | ok | same | same | same | 1727/1727 | 0 | 0 |
| bridge-loaded-base-1 | 23983475 | ok | same | same | same | 1733/1733 | 212 | 892 |
| bridge-loaded-base-2 | 23983476 | ok | same | same | same | 1729/1729 | 213 | 891 |
| bridge-loaded-host-fetch-1 | 23983481 | ok | different | different | different | 0/0 | 212 | 891 |
| bridge-loaded-timer-engine-1 | 23983479 | ok | different | different | different | 173/1235 | 212 | 892 |
| bridge-loaded-timer-host-1 | 23983478 | ok | different | different | different | 1586/1730 | 212 | 892 |
| pins-idle-base-1 | 23983461 | ok | same | same | same | 580/580 | 0 | 0 |
| pins-idle-host-fetch-1 | 23983470 | ok | different | different | different | 0/0 | 0 | 0 |
| pins-idle-timer-engine-1 | 23983467 | ok | same | same | same | 580/580 | 0 | 0 |
| pins-idle-timer-host-1 | 23983465 | ok | same | same | same | 580/580 | 0 | 0 |
| pins-loaded-base-1 | 23983462 | ok | same | same | same | 580/580 | 9612 | 309 |
| pins-loaded-base-2 | 23983463 | ok | same | same | same | 580/580 | 9714 | 309 |
| pins-loaded-base-3 | 23983464 | ok | same | same | same | 580/580 | 9746 | 309 |
| pins-loaded-engine-fetch-1 | 23983472 | ok | different | different | different | 0/0 | 9612 | 309 |
| pins-loaded-host-fetch-1 | 23983471 | ok | different | different | different | 0/0 | 9612 | 309 |
| pins-loaded-timer-engine-1 | 23983468 | ok | different | different | different | 52/413 | 9612 | 309 |
| pins-loaded-timer-host-1 | 23983466 | ok | different | different | different | 0/376 | 9612 | 309 |
| bridge-chain-1 | 23983483 | ok | pass | - | - | - | - | - |
| four-1 | 23983482 | ok | pass | - | - | - | - | - |

ALL PASS
