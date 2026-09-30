| campaign | generator | level | seeds | cases/seed | host cycles/case | cases run | passed | failed | infra errors | programs loaded + reloads | lockstep cycles | instructions completed | sim CPU-h | Slurm array |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `r16-rtl-line` | line | RTL | 0x1..0x1000 (4096) | 64 | 6,000 | 262,144 | 262,144 | 0 | 0 | 923,253 + 2,028,958 | 2,160,539,004 | 652,339,431 | 164.5 | 24210655 |
| `r16-rtl-line-dense` | line-dense | RTL | 0x300001..0x300100 (256) | 64 | 6,000 | 16,384 | 16,384 | 0 | 0 | 57,542 + 131,117 | 142,627,943 | 36,778,097 | 9.5 | 24210666 |
| `r16-rtl-line-faulty` | line-faulty | RTL | 0x400001..0x400100 (256) | 64 | 6,000 | 16,384 | 16,384 | 0 | 0 | 57,731 + 125,402 | 134,751,258 | 34,165,424 | 9.2 | 24210667 |
| `r16-rtl-line-long` | line | RTL | 0x100001..0x100100 (256) | 16 | 50,000 | 4,096 | 4,096 | 0 | 0 | 14,431 + 261,264 | 214,000,318 | 101,390,066 | 15.3 | 24210668 |
| `r16-rtl-default` | default | RTL | 0x1..0x1000 (4096) | 64 | 2,000 | 262,144 | 262,144 | 0 | 0 | 923,438 + 203,172 | 1,016,818,199 | 468,062,008 | 88.6 | 24210657 |
| `r16-rtl-xcov-default` | default | RTL | 0x1001..0x1400 (1024) | 64 | 2,000 | 65,536 | 65,536 | 0 | 0 | 230,667 + 51,117 | 254,192,597 | 116,293,530 | 20.9 | 24210669 |
| `r16-rtl-long` | default | RTL | 0x100001..0x100100 (256) | 64 | 20,000 | 16,384 | 16,384 | 0 | 0 | 57,731 + 125,761 | 358,593,524 | 297,279,846 | 35.4 | 24296483 |
| `r16-rtl-xcov-dense` | dense | RTL | 0x300101..0x300200 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,596 + 52,832 | 178,664,383 | 38,299,000 | 13.9 | 24296958 |
| `r16-rtl-xcov-faulty` | faulty | RTL | 0x400101..0x400200 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,654 + 45,574 | 161,873,137 | 95,958,756 | 11.2 | 24296959 |
| `r16-rtl-xcov-hostile` | hostile | RTL | 0x500001..0x500100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,579 + 35,850 | 160,617,203 | 78,320,084 | 11.2 | 24296960 |
| `r16-rtl-xcov-deselect` | deselect | RTL | 0x600001..0x600100 (256) | 64 | 2,000 | 16,384 | 16,384 | 0 | 0 | 57,481 + 13,170 | 62,814,773 | 18,108,169 | 7.0 | 24296493 |
| `r16-gl-line` | line | GL | 0x1..0x80 (128) | 8 | 6,000 | 1,024 | 1,024 | 0 | 0 | 3,609 + 7,884 | 8,438,586 | 2,463,616 | 5.3 | 24210659 |
| `r16-gl-line-extended` | line | GL | 0x81..0x180 (256) | 16 | 6,000 | 4,096 | 4,096 | 0 | 0 | 14,414 + 31,851 | 33,717,963 | 10,375,224 | 18.5 | 24296549 |
| `r16-gl-default` | default | GL | 0x1..0x40 (64) | 8 | 2,000 | 512 | 512 | 0 | 0 | 1,830 + 375 | 1,993,883 | 981,519 | 0.9 | 24210660 |
| `r16-gl-extended` | default | GL | 0x41..0x140 (256) | 32 | 2,000 | 8,192 | 8,192 | 0 | 0 | 28,882 + 6,279 | 31,754,483 | 14,522,681 | 24.4 | 24296494 |
| `r16-gl-hostile` | hostile | GL | 0x500001..0x500040 (64) | 16 | 8,000 | 1,024 | 1,024 | 0 | 0 | 3,613 + 2,116 | 10,039,757 | 5,008,264 | 6.3 | 24296961 |
| `r16-gl-deselect` | deselect | GL | 0x600001..0x600040 (64) | 16 | 2,000 | 1,024 | 1,024 | 0 | 0 | 3,572 + 795 | 3,920,240 | 1,087,926 | 1.9 | 24296548 |
| **total** | | | 12,096 seeds | | | **724,480** | **724,480** | **0** | 0 | 2,551,023 + 3,123,517 | **4,935,357,251** | 1,971,433,641 | 443.9 | |

Merged coverage, all campaigns (bins from `random_gen.Coverage`, counted on the model in lockstep):

| opcode | engine 0 | engine 1 | engine 2 | engine 3 |
|---|---:|---:|---:|---:|
| NOP | 4,790,863 | 4,571,531 | 4,234,770 | 4,024,665 |
| HALT | 329,781 | 327,583 | 326,483 | 324,304 |
| SET | 42,566,599 | 40,597,001 | 38,328,865 | 35,960,164 |
| DIR | 21,393,628 | 20,593,907 | 19,450,069 | 18,482,311 |
| WAIT | 9,505,910 | 9,084,387 | 8,600,990 | 8,187,963 |
| JMP | 114,419,993 | 110,247,970 | 103,782,622 | 99,520,234 |
| PULL | 1,446,549 | 1,435,978 | 1,417,101 | 1,398,588 |
| PUSH | 5,112,998 | 5,069,092 | 5,010,839 | 4,924,643 |
| OUT | 21,378,604 | 20,500,442 | 18,876,011 | 17,625,267 |
| IN | 15,791,834 | 15,274,797 | 14,305,456 | 13,487,727 |
| COUNT | 5,749,637 | 5,409,489 | 5,226,147 | 4,817,875 |
| LOOP | 6,062,251 | 5,774,769 | 5,490,124 | 5,232,660 |
| LIMIT | 25,110,388 | 24,172,424 | 22,711,941 | 21,577,702 |
| WAITPIN | 4,552,726 | 4,377,783 | 4,076,598 | 3,860,264 |
| SIGNAL | 7,095,256 | 6,719,491 | 6,371,635 | 5,971,539 |
| WAITEVENT | 812,199 | 745,184 | 745,769 | 694,071 |
| PINS | 49,476,970 | 47,283,945 | 44,314,587 | 41,803,243 |
| XFER | 8,465,937 | 8,114,883 | 7,775,009 | 7,451,637 |
| MOV | 23,052,007 | 21,678,128 | 20,750,194 | 19,451,249 |
| LOAD | 16,314,476 | 15,753,707 | 15,211,035 | 14,507,468 |
| ADD | 5,976,813 | 5,712,309 | 5,235,224 | 5,028,426 |
| XOR | 9,423,734 | 9,044,257 | 8,355,940 | 8,063,035 |
| AND | 6,400,725 | 5,931,868 | 5,642,565 | 5,250,079 |
| OR | 5,861,079 | 5,966,867 | 5,312,197 | 5,104,509 |
| SHL | 4,235,281 | 4,024,798 | 3,715,021 | 3,546,201 |
| SHR | 3,590,645 | 3,428,648 | 3,286,576 | 3,033,898 |
| JZ | 60,903,799 | 58,353,622 | 53,540,853 | 50,258,744 |
| NOT | 4,738,444 | 4,604,755 | 4,359,038 | 4,172,810 |
| TIME | 6,923,461 | 6,590,578 | 6,217,654 | 5,825,944 |

Stall cycles: PULL 718,947,029, PUSH 1,144,065,602, WAITPIN 296,959,510, WAITEVENT 328,887,851

Faults (code from instruction): code 1 from DIR 122,683, code 1 from INVALID 603,594, code 1 from LIMIT 57,644, code 1 from MOV 58,147, code 1 from NOP 57,468, code 1 from OUT 118,016, code 1 from PINS 57,257, code 1 from PUSH 56,895, code 1 from SET 71,218, code 1 from SHL 277,605, code 1 from SHR 222,274, code 1 from SIGNAL 56,665, code 1 from XFER 3,164,363, code 2 from PC-RANGE 1,090,000, code 3 from WAITEVENT 165,074, code 3 from WAITPIN 172,385, code 4 from PUSH 413,458; explicit `FAULT n`: 255 distinct codes, 294,324 faults

XFER: 32/32 CPOL/CPHA/bit-order/drive/sample combinations issued (min 369,977, max 4,168,904 per combination); bits=1 7,009,544, bits 2..width-1 24,458,113, bits=width 1,648,891

Mover (DMA) word transfers: 1,955,078

Host commands: BEGIN accepted 5,933,945, BEGIN rejected 189,002, CLEAR accepted 6,549,663, CLEAR rejected 679,438, COMMIT accepted 5,816,833, COMMIT rejected 387,093, EVENT accepted 2,777,845, EVENT rejected 141,716, FLUSH accepted 154,922, FLUSH rejected 122,256, OWN accepted 5,741,333, OWN rejected 209,650, READ_SELECT accepted 58,899,602, READ_SELECT rejected 184,971, ROUTE accepted 1,955,993, ROUTE rejected 142,186, SELECT accepted 85,076,178, SELECT rejected 184,739, START accepted 12,408,984, START rejected 2,236,043, STOP accepted 1,558,446, STOP rejected 141,152, TRIGGER accepted 1,034,431, TRIGGER rejected 245,070, invalid-op rejected 278,092

Host traffic: deselect (ena low) 88,168, engine reprogrammed 3,123,517, partial read abandoned (window 0) 1,041,270, partial read abandoned (window 3) 1,041,608, partial write abandoned (window 0) 693,671, partial write abandoned (window 1) 4,081,232, partial write abandoned (window 2) 692,475, program word not accepted 130,624, program word written 2,370,943, revive: cleared fault 5,759,381, revive: restarted 7,475,045, rx read abandoned (empty) 4,015,364, rx word read 13,234,063, status read select=0 868,302, status read select=1 867,125, status read select=2 866,893, status read select=3 866,899, status read select=4 867,306, status read select=5 868,615, status read select=6 866,159, status read select=7 868,134, tx word accepted 10,868,845, tx write abandoned (full) 7,167,834

Other: byte-lane shift count faults (code 1) 441,913, cycles with IRQ asserted 4,548,141,490, cycles with any pin driven 2,822,310,385, cycles with fault output asserted 4,021,035,289, engine starts 12,112,333, jump targets saturated to PC 127 309,729

Holes (bins never hit), all campaigns: `{"opcode_x_engine": {"0": [], "1": [], "2": [], "3": []}, "engines_without_any_completion": [], "stall_kinds": [], "fault_bins": [], "xfer_flag_combinations": [], "xfer_bit_counts": [], "host_commands": [], "host_traffic": [], "misc": [], "xcov": [], "lcov": []}`

Holes, upstream generator only (`r16-rtl-default`): `{"opcode_x_engine": {"0": [], "1": [], "2": [], "3": []}, "engines_without_any_completion": [], "stall_kinds": [], "fault_bins": [], "xfer_flag_combinations": [], "xfer_bit_counts": [], "host_commands": [], "host_traffic": [], "misc": []}`

Failures: none

Extended cross coverage (`xcov.py`): hits per campaign, and hits per 1,000 cases in parentheses; sorted by the rarest rate over all xcov campaigns.

| bin | `r16-rtl-xcov-default` (65,536 cases) | `r16-rtl-xcov-dense` (16,384 cases) | `r16-rtl-xcov-faulty` (16,384 cases) | `r16-rtl-xcov-hostile` (16,384 cases) | `r16-rtl-xcov-deselect` (16,384 cases) |
|---|---:|---:|---:|---:|---:|
| synchronous start of 4 engines | 26 (0.397) | 25 (1.53) | 7 (0.427) | 8 (0.488) | 2 (0.122) |
| mover blocked: host TX write to dest, same edge | 134 (2.04) | 53 (3.23) | 24 (1.46) | 44 (2.69) | 32 (1.95) |
| host TX write + engine PULL on same FIFO, same edge | 114 (1.74) | 143 (8.73) | 44 (2.69) | 75 (4.58) | 28 (1.71) |
| synchronous start of 3 engines | 251 (3.83) | 278 (17) | 83 (5.07) | 119 (7.26) | 29 (1.77) |
| mover arbitration: >=2 routes eligible | 90 (1.37) | 509 (31.1) | 84 (5.13) | 140 (8.54) | 22 (1.34) |
| WAITPIN/WAITEVENT timeout with LIMIT 1 | 659 (10.1) | 736 (44.9) | 495 (30.2) | 321 (19.6) | 105 (6.41) |
| engines in XFER: 4 | 970 (14.8) | 763 (46.6) | 113 (6.9) | 579 (35.3) | 35 (2.14) |
| mover push into FIFO the host writes next edge (dest == selected, window 2) | 1,156 (17.6) | 873 (53.3) | 286 (17.5) | 431 (26.3) | 329 (20.1) |
| mover push + engine PULL on same dest, same edge | 1,264 (19.3) | 1,052 (64.2) | 518 (31.6) | 589 (35.9) | 228 (13.9) |
| EVENT command and SIGNAL on same edge | 1,524 (23.3) | 598 (36.5) | 1,246 (76) | 957 (58.4) | 213 (13) |
| synchronous start of 2 engines | 1,778 (27.1) | 1,508 (92) | 697 (42.5) | 840 (51.3) | 206 (12.6) |
| host RX pop + engine PUSH on same FIFO, same edge | 3,028 (46.2) | 3,903 (238) | 1,690 (103) | 1,890 (115) | 414 (25.3) |
| STOP/BEGIN of engine inside WAIT | 4,705 (71.8) | 2,069 (126) | 1,858 (113) | 2,282 (139) | 406 (24.8) |
| FLUSH cleared an active route | 2,728 (41.6) | 3,981 (243) | 3,172 (194) | 2,307 (141) | 460 (28.1) |
| strict PUSH overflow (fault 4) while host holds that RX head | 3,481 (53.1) | 6,131 (374) | 2,209 (135) | 2,164 (132) | 483 (29.5) |
| mover pop + engine PUSH on same source, same edge | 5,862 (89.4) | 3,706 (226) | 1,779 (109) | 2,356 (144) | 1,305 (79.7) |
| reset/deselect inside XFER | 11,501 (175) | 3,044 (186) | 2,450 (150) | 2,693 (164) | 3,277 (200) |
| reset/deselect with engines running | 5,803 (88.5) | 1,243 (75.9) | 1,393 (85) | 1,322 (80.7) | 14,499 (885) |
| mover route exhausted (count reached 0) | 11,841 (181) | 7,995 (488) | 3,632 (222) | 4,790 (292) | 2,449 (149) |
| mover blocked: source RX head reserved by host read | 11,259 (172) | 12,012 (733) | 2,479 (151) | 5,545 (338) | 1,568 (95.7) |
| STOP/BEGIN of engine inside XFER | 15,813 (241) | 8,796 (537) | 6,194 (378) | 7,952 (485) | 1,299 (79.3) |
| host RX read abandoned mid-word (window change) | 13,901 (212) | 20,051 (1.22e+03) | 10,989 (671) | 10,189 (622) | 1,892 (115) |
| ROUTE edited an active route | 16,093 (246) | 15,789 (964) | 14,732 (899) | 10,938 (668) | 2,755 (168) |
| trigger delivered to engine blocked in WAITEVENT | 21,494 (328) | 8,587 (524) | 15,700 (958) | 13,281 (811) | 3,092 (189) |
| mover route to self moved a word | 26,480 (404) | 18,596 (1.14e+03) | 8,408 (513) | 11,334 (692) | 5,271 (322) |
| host fault flag cleared | 37,523 (573) | 17,472 (1.07e+03) | 15,164 (926) | 17,192 (1.05e+03) | 9,686 (591) |
| HALT with output enables active | 33,415 (510) | 17,603 (1.07e+03) | 35,173 (2.15e+03) | 17,294 (1.06e+03) | 5,855 (357) |
| WAITEVENT satisfied after blocking | 47,548 (726) | 30,062 (1.83e+03) | 30,989 (1.89e+03) | 25,468 (1.55e+03) | 7,048 (430) |
| STOP/BEGIN of engine stalled on PULL/PUSH/WAITPIN/WAITEVENT | 75,538 (1.15e+03) | 40,798 (2.49e+03) | 29,298 (1.79e+03) | 38,696 (2.36e+03) | 5,390 (329) |
| host fault flag set | 90,904 (1.39e+03) | 32,919 (2.01e+03) | 30,709 (1.87e+03) | 34,577 (2.11e+03) | 27,613 (1.69e+03) |
| engines in XFER: 3 | 72,962 (1.11e+03) | 79,278 (4.84e+03) | 28,841 (1.76e+03) | 38,150 (2.33e+03) | 10,597 (647) |
| WAITEVENT consumed event with simultaneous new delivery | 78,459 (1.2e+03) | 35,399 (2.16e+03) | 66,106 (4.03e+03) | 51,678 (3.15e+03) | 8,994 (549) |
| WAIT >= 20 cycles issued | 76,985 (1.17e+03) | 58,261 (3.56e+03) | 50,587 (3.09e+03) | 48,781 (2.98e+03) | 12,284 (750) |
| mover moved a word | 105,325 (1.61e+03) | 66,496 (4.06e+03) | 31,145 (1.9e+03) | 41,275 (2.52e+03) | 22,065 (1.35e+03) |
| XFER issued with half-period >= 4 | 133,238 (2.03e+03) | 90,862 (5.55e+03) | 92,433 (5.64e+03) | 79,673 (4.86e+03) | 21,466 (1.31e+03) |
| fault with output enables active | 94,280 (1.44e+03) | 118,389 (7.23e+03) | 206,700 (1.26e+04) | 57,802 (3.53e+03) | 16,956 (1.03e+03) |
| WAITEVENT satisfied on issue | 185,227 (2.83e+03) | 130,062 (7.94e+03) | 133,943 (8.18e+03) | 124,530 (7.6e+03) | 26,099 (1.59e+03) |
| WAITPIN satisfied after blocking | 305,853 (4.67e+03) | 135,459 (8.27e+03) | 223,642 (1.37e+04) | 211,660 (1.29e+04) | 47,447 (2.9e+03) |
| XFER issued with half-period 1 | 566,548 (8.64e+03) | 274,093 (1.67e+04) | 428,492 (2.62e+04) | 367,959 (2.25e+04) | 89,159 (5.44e+03) |
| WAITPIN satisfied on issue | 822,442 (1.25e+04) | 303,100 (1.85e+04) | 615,034 (3.75e+04) | 572,981 (3.5e+04) | 130,320 (7.95e+03) |
| LOOP taken | 789,210 (1.2e+04) | 599,078 (3.66e+04) | 544,417 (3.32e+04) | 490,741 (3e+04) | 121,190 (7.4e+03) |
| mover arbitration: 4 routes configured | 139,765 (2.13e+03) | 932,197 (5.69e+04) | 946,265 (5.78e+04) | 553,943 (3.38e+04) | 2,870 (175) |
| JZ not taken | 1,027,178 (1.57e+04) | 652,188 (3.98e+04) | 659,341 (4.02e+04) | 651,079 (3.97e+04) | 144,983 (8.85e+03) |
| LOOP fell through | 954,150 (1.46e+04) | 741,176 (4.52e+04) | 781,536 (4.77e+04) | 631,969 (3.86e+04) | 149,499 (9.12e+03) |
| engines in XFER: 2 | 2,244,045 (3.42e+04) | 1,991,032 (1.22e+05) | 985,896 (6.02e+04) | 1,495,107 (9.13e+04) | 264,034 (1.61e+04) |
| trigger mode 1 (fall) detected | 4,006,916 (6.11e+04) | 3,190,438 (1.95e+05) | 2,904,992 (1.77e+05) | 2,718,561 (1.66e+05) | 591,211 (3.61e+04) |
| trigger mode 0 (rise) detected | 4,045,959 (6.17e+04) | 3,484,525 (2.13e+05) | 2,799,183 (1.71e+05) | 2,852,661 (1.74e+05) | 592,379 (3.62e+04) |
| engines running: 4 | 6,330,503 (9.66e+04) | 2,200,883 (1.34e+05) | 3,132,089 (1.91e+05) | 2,443,493 (1.49e+05) | 797,825 (4.87e+04) |
| IRQ from RX data only | 16,192,693 (2.47e+05) | 5,362,585 (3.27e+05) | 4,532,115 (2.77e+05) | 5,001,668 (3.05e+05) | 3,830,924 (2.34e+05) |
| open-drain pin released (logical 1) | 17,800,765 (2.72e+05) | 8,687,666 (5.3e+05) | 9,780,809 (5.97e+05) | 9,587,263 (5.85e+05) | 2,712,405 (1.66e+05) |
| JZ taken | 15,750,919 (2.4e+05) | 8,123,156 (4.96e+05) | 12,482,644 (7.62e+05) | 10,827,114 (6.61e+05) | 2,428,192 (1.48e+05) |
| open-drain pin pulled low | 23,610,108 (3.6e+05) | 13,228,334 (8.07e+05) | 12,892,387 (7.87e+05) | 12,867,887 (7.85e+05) | 3,579,416 (2.18e+05) |
| engines running: 3 | 27,215,731 (4.15e+05) | 13,252,981 (8.09e+05) | 18,206,458 (1.11e+06) | 15,039,095 (9.18e+05) | 3,680,935 (2.25e+05) |
| RX FIFO full e3 | 22,015,272 (3.36e+05) | 20,485,195 (1.25e+06) | 15,251,868 (9.31e+05) | 17,776,582 (1.08e+06) | 2,121,418 (1.29e+05) |
| RX FIFO full e2 | 23,541,503 (3.59e+05) | 21,154,981 (1.29e+06) | 15,489,345 (9.45e+05) | 17,489,780 (1.07e+06) | 2,734,895 (1.67e+05) |
| RX FIFO full e1 | 24,299,804 (3.71e+05) | 22,385,680 (1.37e+06) | 15,394,454 (9.4e+05) | 17,984,675 (1.1e+06) | 3,344,172 (2.04e+05) |
| RX FIFO full e0 | 25,703,481 (3.92e+05) | 22,371,039 (1.37e+06) | 14,472,595 (8.83e+05) | 18,659,838 (1.14e+06) | 3,936,057 (2.4e+05) |
| trigger mode 2 (high) detected | 27,911,480 (4.26e+05) | 21,729,884 (1.33e+06) | 20,039,696 (1.22e+06) | 18,876,837 (1.15e+06) | 4,023,994 (2.46e+05) |
| trigger mode 3 (low) detected | 29,491,781 (4.5e+05) | 23,039,843 (1.41e+06) | 20,115,057 (1.23e+06) | 19,571,268 (1.19e+06) | 4,023,031 (2.46e+05) |
| mover blocked: dest TX FIFO full | 25,734,164 (3.93e+05) | 46,924,989 (2.86e+06) | 30,342,113 (1.85e+06) | 30,989,260 (1.89e+06) | 2,443,301 (1.49e+05) |
| engines running: 2 | 53,380,286 (8.15e+05) | 36,928,879 (2.25e+06) | 42,574,182 (2.6e+06) | 38,688,824 (2.36e+06) | 8,064,111 (4.92e+05) |
| push-pull pin driven high | 56,282,391 (8.59e+05) | 36,149,061 (2.21e+06) | 45,735,055 (2.79e+06) | 39,090,296 (2.39e+06) | 8,899,716 (5.43e+05) |
| engines running: 1 | 62,420,020 (9.52e+05) | 59,003,262 (3.6e+06) | 51,413,491 (3.14e+06) | 52,959,754 (3.23e+06) | 12,764,334 (7.79e+05) |
| push-pull pin driven low | 72,020,255 (1.1e+06) | 52,387,992 (3.2e+06) | 56,653,417 (3.46e+06) | 49,185,348 (3e+06) | 11,225,002 (6.85e+05) |
| IRQ from event only | 81,577,594 (1.24e+06) | 38,214,495 (2.33e+06) | 54,857,178 (3.35e+06) | 46,910,142 (2.86e+06) | 24,618,375 (1.5e+06) |
| TX FIFO full e3 | 58,502,643 (8.93e+05) | 84,491,396 (5.16e+06) | 84,627,866 (5.17e+06) | 73,118,070 (4.46e+06) | 3,940,678 (2.41e+05) |
| TX FIFO full e2 | 60,434,902 (9.22e+05) | 84,359,854 (5.15e+06) | 85,169,699 (5.2e+06) | 72,310,487 (4.41e+06) | 4,278,666 (2.61e+05) |
| engines running: 0 | 104,551,641 (1.6e+06) | 67,204,915 (4.1e+06) | 46,473,532 (2.84e+06) | 51,412,257 (3.14e+06) | 37,374,898 (2.28e+06) |
| TX FIFO full e1 | 61,587,369 (9.4e+05) | 85,110,082 (5.19e+06) | 86,148,411 (5.26e+06) | 72,594,158 (4.43e+06) | 4,714,675 (2.88e+05) |
| TX FIFO full e0 | 63,693,591 (9.72e+05) | 84,923,528 (5.18e+06) | 85,860,782 (5.24e+06) | 73,079,643 (4.46e+06) | 5,227,685 (3.19e+05) |
| IRQ from event and RX data | 127,705,695 (1.95e+06) | 126,067,138 (7.69e+06) | 94,433,108 (5.76e+06) | 100,612,123 (6.14e+06) | 16,341,202 (9.97e+05) |

xcov observer errors: none

Line-unit coverage, all campaigns: 357 bins, 357 hit, 0 holes, 0 declared unreachable; 724,480 cases.

| bin | hits | per 1,000 cases |
|---|---:|---:|
| LTIM P=0 (stops) | 1,551,170 | 2.14e+03 |
| LTIM P=1 | 10,120,999 | 1.4e+04 |
| LTIM P=2 | 8,316,757 | 1.15e+04 |
| LTIM P=3..15 | 10,847,988 | 1.5e+04 |
| LTIM P=16..127 | 3,283,333 | 4.53e+03 |
| LTIM P=128..253 | 824,916 | 1.14e+03 |
| LTIM P=254 | 668,140 | 922 |
| LTIM P=255 | 627,987 | 867 |
| LTIM Q=0 | 16,592,287 | 2.29e+04 |
| LTIM Q=1 | 995,920 | 1.37e+03 |
| LTIM Q=2..127 | 8,434,278 | 1.16e+04 |
| LTIM Q=128 | 996,696 | 1.38e+03 |
| LTIM Q=129..254 | 8,246,963 | 1.14e+04 |
| LTIM Q=255 | 975,146 | 1.35e+03 |
| LTIM D=0 (first tick after P) | 18,064,564 | 2.49e+04 |
| LTIM D=1 | 3,380,201 | 4.67e+03 |
| LTIM D=2..15 | 8,458,700 | 1.17e+04 |
| LTIM D=16..254 | 4,710,880 | 6.5e+03 |
| LTIM D=255 | 1,626,945 | 2.25e+03 |
| LTIM P=255 with Q=0 (valid) | 627,987 | 867 |
| LTIM restarts a running ticker | 24,871,049 | 3.43e+04 |
| LTIM P=0 stops a running ticker | 763,176 | 1.05e+03 |
| LTIM invalid: P=255 with Q!=0 | 34,407 | 47.5 |
| tick: bit boundary | 499,630,872 | 6.9e+05 |
| tick: mid-bit | 488,059,048 | 6.74e+05 |
| tick with fraction carry (P+1 cycles) | 205,025,348 | 2.83e+05 |
| tick with fraction carry at P=254 (255 cycles) | 69,845 | 96.4 |
| ticks in consecutive cycles (P=1) | 435,382,568 | 6.01e+05 |
| ticker runs during WAIT | 10,002,297 | 1.38e+04 |
| ticker runs while stalled on PULL | 195,453,798 | 2.7e+05 |
| ticker runs while stalled on PUSH | 187,259,773 | 2.58e+05 |
| ticker runs while stalled on WAITPIN | 56,830,974 | 7.84e+04 |
| ticker runs while stalled on WAITEVENT | 21,784,445 | 3.01e+04 |
| classic XFER stops a running ticker | 3,235,506 | 4.47e+03 |
| LCFG line code NRZ | 10,298,846 | 1.42e+04 |
| LCFG line code NRZI | 15,832,693 | 2.19e+04 |
| LCFG line code Manchester | 9,284,800 | 1.28e+04 |
| LCFG stuffing, runs of 1s, length 1 | 913,788 | 1.26e+03 |
| LCFG stuffing, runs of 1s, length 2 | 1,576,368 | 2.18e+03 |
| LCFG stuffing, runs of 1s, length 3 | 1,557,873 | 2.15e+03 |
| LCFG stuffing, runs of 1s, length 4 | 1,603,527 | 2.21e+03 |
| LCFG stuffing, runs of 1s, length 5 | 1,599,877 | 2.21e+03 |
| LCFG stuffing, runs of 1s, length 6 | 1,585,452 | 2.19e+03 |
| LCFG stuffing, runs of 1s, length 7 | 939,355 | 1.3e+03 |
| LCFG stuffing, runs of 1s, length 8 | 952,466 | 1.31e+03 |
| LCFG stuffing, runs of either polarity, length 2 | 1,540,115 | 2.13e+03 |
| LCFG stuffing, runs of either polarity, length 3 | 1,534,145 | 2.12e+03 |
| LCFG stuffing, runs of either polarity, length 4 | 1,528,440 | 2.11e+03 |
| LCFG stuffing, runs of either polarity, length 5 | 1,589,738 | 2.19e+03 |
| LCFG stuffing, runs of either polarity, length 6 | 1,493,953 | 2.06e+03 |
| LCFG stuffing, runs of either polarity, length 7 | 897,528 | 1.24e+03 |
| LCFG stuffing, runs of either polarity, length 8 | 888,235 | 1.23e+03 |
| LCFG pair | 14,655,819 | 2.02e+04 |
| LCFG arbitration monitor | 10,870,511 | 1.5e+04 |
| LCFG SE0 end | 11,062,685 | 1.53e+04 |
| LCFG initial level 0 | 17,834,230 | 2.46e+04 |
| LCFG initial level 1 | 17,582,109 | 2.43e+04 |
| LCFG polarity bit without stuffing (ignored) | 982,006 | 1.36e+03 |
| LCFG clears set flags (SE0, lost, stuff error) | 677,692 | 935 |
| LCFG invalid: line code 3 | 51,618 | 71.2 |
| LCFG invalid: bits 23..11 set | 34,068 | 47 |
| LCFG invalid: stuffing on either polarity with run length 1 | 36,168 | 49.9 |
| CRC set from r0 | 993,330 | 1.37e+03 |
| CRC set from r1 | 979,670 | 1.35e+03 |
| CRC set from r2 | 18,681,943 | 2.58e+04 |
| CRC set from r3 | 1,035,167 | 1.43e+03 |
| CRC read into r0 | 1,887,895 | 2.61e+03 |
| CRC read into r1 | 3,355,843 | 4.63e+03 |
| CRC read into r2 | 3,618,628 | 4.99e+03 |
| CRC read into r3 | 3,642,307 | 5.03e+03 |
| CRC preset 0 selected | 7,474,058 | 1.03e+04 |
| CRC preset 1 selected | 7,369,655 | 1.02e+04 |
| CRC preset 2 selected | 7,466,380 | 1.03e+04 |
| CRC preset 3 selected | 7,321,699 | 1.01e+04 |
| CRC invalid: c=0 | 35,042 | 48.4 |
| CRC invalid: c>3 | 34,797 | 48 |
| CRC invalid: set with a!=0 | 34,441 | 47.5 |
| CRC invalid: set with b>3 | 34,496 | 47.6 |
| CRC invalid: read with a>3 | 33,913 | 46.8 |
| CRC invalid: read with b!=0 | 34,332 | 47.4 |
| CRC invalid: preset with a!=0 | 34,297 | 47.3 |
| CRC invalid: preset with b>3 | 34,290 | 47.3 |
| CRC fed: preset 0, LSB first, by line drive | 4,996,578 | 6.9e+03 |
| CRC fed: preset 0, LSB first, by line sample | 4,370,741 | 6.03e+03 |
| CRC fed: preset 0, LSB first, by classic drive | 243,245 | 336 |
| CRC fed: preset 0, LSB first, by classic sample | 460,416 | 636 |
| CRC fed: preset 0, MSB first, by line drive | 4,912,361 | 6.78e+03 |
| CRC fed: preset 0, MSB first, by line sample | 4,406,124 | 6.08e+03 |
| CRC fed: preset 0, MSB first, by classic drive | 243,608 | 336 |
| CRC fed: preset 0, MSB first, by classic sample | 464,528 | 641 |
| CRC fed: preset 1, LSB first, by line drive | 4,845,529 | 6.69e+03 |
| CRC fed: preset 1, LSB first, by line sample | 4,398,294 | 6.07e+03 |
| CRC fed: preset 1, LSB first, by classic drive | 242,712 | 335 |
| CRC fed: preset 1, LSB first, by classic sample | 446,300 | 616 |
| CRC fed: preset 1, MSB first, by line drive | 4,929,875 | 6.8e+03 |
| CRC fed: preset 1, MSB first, by line sample | 4,338,502 | 5.99e+03 |
| CRC fed: preset 1, MSB first, by classic drive | 233,208 | 322 |
| CRC fed: preset 1, MSB first, by classic sample | 471,165 | 650 |
| CRC fed: preset 2, LSB first, by line drive | 4,886,092 | 6.74e+03 |
| CRC fed: preset 2, LSB first, by line sample | 4,408,753 | 6.09e+03 |
| CRC fed: preset 2, LSB first, by classic drive | 254,181 | 351 |
| CRC fed: preset 2, LSB first, by classic sample | 468,075 | 646 |
| CRC fed: preset 2, MSB first, by line drive | 4,838,235 | 6.68e+03 |
| CRC fed: preset 2, MSB first, by line sample | 4,330,855 | 5.98e+03 |
| CRC fed: preset 2, MSB first, by classic drive | 235,410 | 325 |
| CRC fed: preset 2, MSB first, by classic sample | 457,404 | 631 |
| CRC fed: preset 3, LSB first, by line drive | 4,856,905 | 6.7e+03 |
| CRC fed: preset 3, LSB first, by line sample | 4,326,957 | 5.97e+03 |
| CRC fed: preset 3, LSB first, by classic drive | 277,661 | 383 |
| CRC fed: preset 3, LSB first, by classic sample | 469,938 | 649 |
| CRC fed: preset 3, MSB first, by line drive | 4,879,792 | 6.74e+03 |
| CRC fed: preset 3, MSB first, by line sample | 4,355,683 | 6.01e+03 |
| CRC fed: preset 3, MSB first, by classic drive | 268,627 | 371 |
| CRC fed: preset 3, MSB first, by classic sample | 479,928 | 662 |
| LSTAT into r0 | 2,499,015 | 3.45e+03 |
| LSTAT into r1 | 3,505,167 | 4.84e+03 |
| LSTAT into r2 | 2,688,854 | 3.71e+03 |
| LSTAT into r3 | 2,610,662 | 3.6e+03 |
| LSTAT invalid: a>3 | 33,951 | 46.9 |
| LSTAT invalid: b!=0 | 34,439 | 47.5 |
| LSTAT invalid: c!=0 | 33,749 | 46.6 |
| LSTAT reads SE0=0 | 11,153,166 | 1.54e+04 |
| LSTAT reads SE0=1 | 150,532 | 208 |
| LSTAT reads arbitration lost=0 | 11,156,913 | 1.54e+04 |
| LSTAT reads arbitration lost=1 | 146,785 | 203 |
| LSTAT reads stuff error=0 | 10,612,988 | 1.46e+04 |
| LSTAT reads stuff error=1 | 690,710 | 953 |
| LSTAT reads TX data=0 | 682,539 | 942 |
| LSTAT reads TX data=1 | 10,621,159 | 1.47e+04 |
| LSTAT reads RX space=0 | 538,253 | 743 |
| LSTAT reads RX space=1 | 10,765,445 | 1.49e+04 |
| LSTAT reads line level=0 | 5,741,774 | 7.93e+03 |
| LSTAT reads line level=1 | 5,561,924 | 7.68e+03 |
| LSTAT reads ticker running=0 | 323,127 | 446 |
| LSTAT reads ticker running=1 | 10,980,571 | 1.52e+04 |
| LSTAT reads data bits left at SE0 > 0 | 150,532 | 208 |
| line XFER started: drive, 1 bits | 894,548 | 1.23e+03 |
| line XFER started: drive, 2 bits | 670,519 | 926 |
| line XFER started: drive, 3 bits | 350,185 | 483 |
| line XFER started: drive, 4 bits | 240,720 | 332 |
| line XFER started: drive, 5 bits | 223,813 | 309 |
| line XFER started: drive, 6 bits | 204,645 | 282 |
| line XFER started: drive, 7 bits | 202,943 | 280 |
| line XFER started: drive, 8 bits | 1,391,856 | 1.92e+03 |
| line XFER started: drive, 9 bits | 187,300 | 259 |
| line XFER started: drive, 10 bits | 179,930 | 248 |
| line XFER started: drive, 11 bits | 171,292 | 236 |
| line XFER started: drive, 12 bits | 177,923 | 246 |
| line XFER started: drive, 13 bits | 66,144 | 91.3 |
| line XFER started: drive, 14 bits | 63,185 | 87.2 |
| line XFER started: drive, 15 bits | 61,872 | 85.4 |
| line XFER started: drive, 16 bits | 1,061,764 | 1.47e+03 |
| line XFER started: drive, 17 bits | 58,588 | 80.9 |
| line XFER started: drive, 18 bits | 56,838 | 78.5 |
| line XFER started: drive, 19 bits | 57,656 | 79.6 |
| line XFER started: drive, 20 bits | 56,735 | 78.3 |
| line XFER started: drive, 21 bits | 54,591 | 75.4 |
| line XFER started: drive, 22 bits | 53,589 | 74 |
| line XFER started: drive, 23 bits | 53,176 | 73.4 |
| line XFER started: drive, 24 bits | 52,593 | 72.6 |
| line XFER started: drive, 25 bits | 51,266 | 70.8 |
| line XFER started: drive, 26 bits | 51,644 | 71.3 |
| line XFER started: drive, 27 bits | 51,171 | 70.6 |
| line XFER started: drive, 28 bits | 49,807 | 68.7 |
| line XFER started: drive, 29 bits | 49,022 | 67.7 |
| line XFER started: drive, 30 bits | 48,047 | 66.3 |
| line XFER started: drive, 31 bits | 302,328 | 417 |
| line XFER started: drive, 32 bits | 301,201 | 416 |
| line XFER started: sample, 1 bits | 450,385 | 622 |
| line XFER started: sample, 2 bits | 363,053 | 501 |
| line XFER started: sample, 3 bits | 196,726 | 272 |
| line XFER started: sample, 4 bits | 141,264 | 195 |
| line XFER started: sample, 5 bits | 128,081 | 177 |
| line XFER started: sample, 6 bits | 123,097 | 170 |
| line XFER started: sample, 7 bits | 119,343 | 165 |
| line XFER started: sample, 8 bits | 827,157 | 1.14e+03 |
| line XFER started: sample, 9 bits | 109,802 | 152 |
| line XFER started: sample, 10 bits | 109,040 | 151 |
| line XFER started: sample, 11 bits | 103,734 | 143 |
| line XFER started: sample, 12 bits | 109,596 | 151 |
| line XFER started: sample, 13 bits | 42,460 | 58.6 |
| line XFER started: sample, 14 bits | 38,928 | 53.7 |
| line XFER started: sample, 15 bits | 39,472 | 54.5 |
| line XFER started: sample, 16 bits | 663,571 | 916 |
| line XFER started: sample, 17 bits | 35,528 | 49 |
| line XFER started: sample, 18 bits | 35,842 | 49.5 |
| line XFER started: sample, 19 bits | 32,977 | 45.5 |
| line XFER started: sample, 20 bits | 35,994 | 49.7 |
| line XFER started: sample, 21 bits | 32,395 | 44.7 |
| line XFER started: sample, 22 bits | 33,481 | 46.2 |
| line XFER started: sample, 23 bits | 32,494 | 44.9 |
| line XFER started: sample, 24 bits | 31,840 | 43.9 |
| line XFER started: sample, 25 bits | 33,372 | 46.1 |
| line XFER started: sample, 26 bits | 32,094 | 44.3 |
| line XFER started: sample, 27 bits | 32,348 | 44.6 |
| line XFER started: sample, 28 bits | 31,477 | 43.4 |
| line XFER started: sample, 29 bits | 33,999 | 46.9 |
| line XFER started: sample, 30 bits | 31,161 | 43 |
| line XFER started: sample, 31 bits | 193,977 | 268 |
| line XFER started: sample, 32 bits | 190,350 | 263 |
| line XFER started: drive+sample, 1 bits | 235,508 | 325 |
| line XFER started: drive+sample, 2 bits | 203,886 | 281 |
| line XFER started: drive+sample, 3 bits | 115,220 | 159 |
| line XFER started: drive+sample, 4 bits | 83,731 | 116 |
| line XFER started: drive+sample, 5 bits | 75,569 | 104 |
| line XFER started: drive+sample, 6 bits | 75,778 | 105 |
| line XFER started: drive+sample, 7 bits | 71,874 | 99.2 |
| line XFER started: drive+sample, 8 bits | 494,498 | 683 |
| line XFER started: drive+sample, 9 bits | 66,981 | 92.5 |
| line XFER started: drive+sample, 10 bits | 64,988 | 89.7 |
| line XFER started: drive+sample, 11 bits | 64,167 | 88.6 |
| line XFER started: drive+sample, 12 bits | 66,486 | 91.8 |
| line XFER started: drive+sample, 13 bits | 25,397 | 35.1 |
| line XFER started: drive+sample, 14 bits | 24,402 | 33.7 |
| line XFER started: drive+sample, 15 bits | 23,315 | 32.2 |
| line XFER started: drive+sample, 16 bits | 398,299 | 550 |
| line XFER started: drive+sample, 17 bits | 22,226 | 30.7 |
| line XFER started: drive+sample, 18 bits | 22,090 | 30.5 |
| line XFER started: drive+sample, 19 bits | 21,192 | 29.3 |
| line XFER started: drive+sample, 20 bits | 22,230 | 30.7 |
| line XFER started: drive+sample, 21 bits | 20,208 | 27.9 |
| line XFER started: drive+sample, 22 bits | 20,924 | 28.9 |
| line XFER started: drive+sample, 23 bits | 20,568 | 28.4 |
| line XFER started: drive+sample, 24 bits | 20,956 | 28.9 |
| line XFER started: drive+sample, 25 bits | 20,700 | 28.6 |
| line XFER started: drive+sample, 26 bits | 20,461 | 28.2 |
| line XFER started: drive+sample, 27 bits | 19,930 | 27.5 |
| line XFER started: drive+sample, 28 bits | 19,873 | 27.4 |
| line XFER started: drive+sample, 29 bits | 20,359 | 28.1 |
| line XFER started: drive+sample, 30 bits | 18,821 | 26 |
| line XFER started: drive+sample, 31 bits | 120,612 | 166 |
| line XFER started: drive+sample, 32 bits | 117,894 | 163 |
| line XFER completed: drive, 1 bit | 887,850 | 1.23e+03 |
| line XFER completed: drive, 2..31 bits | 5,866,952 | 8.1e+03 |
| line XFER completed: drive, 32 bits | 264,636 | 365 |
| line XFER completed: sample, 1 bit | 420,783 | 581 |
| line XFER completed: sample, 2..31 bits | 3,138,083 | 4.33e+03 |
| line XFER completed: sample, 32 bits | 145,367 | 201 |
| line XFER completed: drive+sample, 1 bit | 224,294 | 310 |
| line XFER completed: drive+sample, 2..31 bits | 1,902,854 | 2.63e+03 |
| line XFER completed: drive+sample, 32 bits | 91,301 | 126 |
| line XFER drive, LSB first | 3,774,779 | 5.21e+03 |
| line XFER drive, MSB first | 3,722,112 | 5.14e+03 |
| line XFER sample, LSB first | 2,212,538 | 3.05e+03 |
| line XFER sample, MSB first | 2,202,500 | 3.04e+03 |
| line XFER drive+sample, LSB first | 1,304,039 | 1.8e+03 |
| line XFER drive+sample, MSB first | 1,315,104 | 1.82e+03 |
| line XFER drive, NRZ | 1,480,170 | 2.04e+03 |
| line XFER drive, NRZI | 2,223,771 | 3.07e+03 |
| line XFER drive, Manchester | 3,792,950 | 5.24e+03 |
| line XFER sample, NRZ | 1,779,317 | 2.46e+03 |
| line XFER sample, NRZI | 2,635,721 | 3.64e+03 |
| line XFER drive+sample, NRZ | 1,045,291 | 1.44e+03 |
| line XFER drive+sample, NRZI | 1,573,852 | 2.17e+03 |
| line XFER drive with pair | 2,666,341 | 3.68e+03 |
| line XFER sample with pair | 1,815,874 | 2.51e+03 |
| line XFER drive+sample with pair | 905,865 | 1.25e+03 |
| line XFER drive with CRC feed | 3,740,621 | 5.16e+03 |
| line XFER sample with CRC feed | 2,202,444 | 3.04e+03 |
| line XFER drive+sample with CRC feed | 1,315,282 | 1.82e+03 |
| line XFER with neither drive nor sample | 48,442 | 66.9 |
| line XFER issued before the next boundary (no gap) | 3,589,858 | 4.96e+03 |
| line XFER invalid: b!=0 | 52,834 | 72.9 |
| line XFER invalid: c[1:0]!=0 | 48,197 | 66.5 |
| line XFER invalid: a=0 | 34,343 | 47.4 |
| line XFER invalid: a>32 | 35,072 | 48.4 |
| line XFER invalid: ticker stopped | 737,131 | 1.02e+03 |
| line XFER invalid: Manchester with sampling | 173,211 | 239 |
| line XFER invalid: data pin not owned (drive) | 770,967 | 1.06e+03 |
| line XFER invalid: pair pin not owned (drive with pair) | 819,085 | 1.13e+03 |
| line XFER invalid: pair pin equals data pin (drive with pair) | 132,093 | 182 |
| XFER invalid: c bit 7 | 34,657 | 47.8 |
| classic XFER with CRC feed (c bit 6) | 1,826,733 | 2.52e+03 |
| stuff bit sent: runs of 1s, length 1 | 818,252 | 1.13e+03 |
| stuff bit sent: runs of 1s, length 2 | 628,027 | 867 |
| stuff bit sent: runs of 1s, length 3 | 364,255 | 503 |
| stuff bit sent: runs of 1s, length 4 | 237,571 | 328 |
| stuff bit sent: runs of 1s, length 5 | 169,784 | 234 |
| stuff bit sent: runs of 1s, length 6 | 133,930 | 185 |
| stuff bit sent: runs of 1s, length 7 | 60,186 | 83.1 |
| stuff bit sent: runs of 1s, length 8 | 51,103 | 70.5 |
| stuff bit sent: either polarity, length 2 | 2,029,286 | 2.8e+03 |
| stuff bit sent: either polarity, length 3 | 1,103,223 | 1.52e+03 |
| stuff bit sent: either polarity, length 4 | 745,608 | 1.03e+03 |
| stuff bit sent: either polarity, length 5 | 536,385 | 740 |
| stuff bit sent: either polarity, length 6 | 420,340 | 580 |
| stuff bit sent: either polarity, length 7 | 205,118 | 283 |
| stuff bit sent: either polarity, length 8 | 175,375 | 242 |
| stuff bit removed: runs of 1s, length 1 | 962,431 | 1.33e+03 |
| stuff bit removed: runs of 1s, length 2 | 916,085 | 1.26e+03 |
| stuff bit removed: runs of 1s, length 3 | 596,247 | 823 |
| stuff bit removed: runs of 1s, length 4 | 423,969 | 585 |
| stuff bit removed: runs of 1s, length 5 | 322,748 | 445 |
| stuff bit removed: runs of 1s, length 6 | 263,081 | 363 |
| stuff bit removed: runs of 1s, length 7 | 118,663 | 164 |
| stuff bit removed: runs of 1s, length 8 | 102,275 | 141 |
| stuff bit removed: either polarity, length 2 | 2,280,468 | 3.15e+03 |
| stuff bit removed: either polarity, length 3 | 1,166,179 | 1.61e+03 |
| stuff bit removed: either polarity, length 4 | 779,429 | 1.08e+03 |
| stuff bit removed: either polarity, length 5 | 572,812 | 791 |
| stuff bit removed: either polarity, length 6 | 442,325 | 611 |
| stuff bit removed: either polarity, length 7 | 210,966 | 291 |
| stuff bit removed: either polarity, length 8 | 177,438 | 245 |
| stuff bit sent with NRZ | 2,279,571 | 3.15e+03 |
| stuff bit sent with NRZI | 3,773,835 | 5.21e+03 |
| stuff bit sent with Manchester | 1,625,037 | 2.24e+03 |
| trailing stuff bit sent | 728,486 | 1.01e+03 |
| trailing stuff bit removed | 801,256 | 1.11e+03 |
| stuff error | 8,685,243 | 1.2e+04 |
| stuff error on a trailing stuff bit | 745,289 | 1.03e+03 |
| arbitration lost (NRZ) | 80,818 | 112 |
| arbitration lost (NRZI) | 122,447 | 169 |
| data 0 sent as 1 after arbitration loss | 3,334,579 | 4.6e+03 |
| stuff cell sent as 1 after arbitration loss | 905,723 | 1.25e+03 |
| drive+sample with the monitor on completed without loss | 428,707 | 592 |
| SE0 end: 1 data bit left | 43,217 | 59.7 |
| SE0 end: 2..31 data bits left | 547,543 | 756 |
| SE0 end: 32 data bits left | 22,456 | 31 |
| SE0 end in a drive+sample XFER | 209,739 | 290 |
| SE0 end on a trailing stuff cell | 1,190 | 1.64 |
| SE0 on the pair without SE0 end (ignored) | 5,600,594 | 7.73e+03 |
| Manchester second half | 40,837,960 | 5.64e+04 |
| Manchester second half after the XFER completed | 2,832,116 | 3.91e+03 |
| Manchester second half with pair | 15,525,578 | 2.14e+04 |
| OUT on a Manchester second-half edge | 164,614 | 227 |
| SET on a Manchester second-half edge | 161,914 | 223 |
| classic XFER issued on a Manchester second-half edge | 111,935 | 155 |
| pair drive (complement on the clock-field pin) | 38,418,387 | 5.3e+04 |
| NRZI drive from initial level 1 | 1,890,832 | 2.61e+03 |
| line drive on an open-drain pin | 17,017,974 | 2.35e+04 |
| sampling a pin another engine drives in a line XFER | 5,307,094 | 7.33e+03 |
| sampling the engine's own data pin while driving it | 7,157,410 | 9.88e+03 |
| line XFERs on 2 engines at once | 266,835,664 | 3.68e+05 |
| line XFERs on 3 engines at once | 42,491,800 | 5.87e+04 |
| line XFERs on 4 engines at once | 2,893,329 | 3.99e+03 |
| mover moved a word during a line XFER | 540,404 | 746 |
| host TX write to an engine in a line XFER | 1,153,328 | 1.59e+03 |
| host RX read from an engine in a sampling line XFER | 961,030 | 1.33e+03 |
| STOP of an engine in a line XFER | 353,742 | 488 |
| BEGIN of an engine in a line XFER | 508,477 | 702 |
| reset or deselect during a line XFER | 16,738 | 23.1 |
| START clears non-zero line-unit state | 5,720,922 | 7.9e+03 |
| fault with the ticker running (unit state kept) | 3,398,771 | 4.69e+03 |
| LTIM while a Manchester second half is pending | 291,612 | 403 |
| LCFG while a Manchester second half is pending | 368,138 | 508 |
| classic XFER while a Manchester second half is pending | 324,227 | 448 |
| START while a Manchester second half is pending | 15,506 | 21.4 |
| tick with fraction carry at P=1 (2 cycles) | 104,245,575 | 1.44e+05 |
| line XFER completed with a fraction (Q!=0): drive | 3,546,450 | 4.9e+03 |
| line XFER completed with a fraction (Q!=0): sample | 1,889,201 | 2.61e+03 |
| line XFER completed with a fraction (Q!=0): drive+sample | 1,138,198 | 1.57e+03 |
| line XFER completed after a trailing stuff bit: drive | 449,796 | 621 |
| line XFER completed after a trailing stuff bit: sample | 523,922 | 723 |
| line XFER completed after a trailing stuff bit: drive+sample | 277,334 | 383 |
| protocol-shaped: NRZI, stuffing runs of 1s length 6, pair, SE0 end, sampling XFER ended | 15,994 | 22.1 |
| protocol-shaped: NRZ, stuffing either polarity length 5, monitor, drive+sample XFER completed | 14,399 | 19.9 |
| protocol-shaped: Manchester drive with pair completed | 1,356,905 | 1.87e+03 |

Decode-check disagreements and observer errors: none

Decode notes: decode note: sample-only line XFER with pair, pair pin not owned or equal to the data pin, accepted: 497,828

Other campaigns with the line-unit bins (not in the table below; bins in their summaries): `r16-rtl-default` (26 bins hit, decode-check disagreements: none), `r16-rtl-xcov-default` (21 bins hit, decode-check disagreements: none), `r16-rtl-long` (15 bins hit, decode-check disagreements: none), `r16-rtl-xcov-dense` (19 bins hit, decode-check disagreements: none), `r16-rtl-xcov-faulty` (14 bins hit, decode-check disagreements: none), `r16-rtl-xcov-hostile` (19 bins hit, decode-check disagreements: none), `r16-rtl-xcov-deselect` (13 bins hit, decode-check disagreements: none), `r16-gl-default` (0 bins hit, decode-check disagreements: none), `r16-gl-extended` (15 bins hit, decode-check disagreements: none), `r16-gl-hostile` (0 bins hit, decode-check disagreements: none), `r16-gl-deselect` (0 bins hit, decode-check disagreements: none)

Line-unit bins per campaign of line cases: hits, and hits per 1,000 cases in parentheses.

| bin | `r16-rtl-line` (262,144 cases) | `r16-rtl-line-dense` (16,384 cases) | `r16-rtl-line-faulty` (16,384 cases) | `r16-rtl-line-long` (4,096 cases) | `r16-gl-line` (1,024 cases) | `r16-gl-line-extended` (4,096 cases) |
|---|---:|---:|---:|---:|---:|---:|
| LTIM P=0 (stops) | 1,256,496 (4.79e+03) | 49,828 (3.04e+03) | 56,386 (3.44e+03) | 159,157 (3.89e+04) | 5,862 (5.72e+03) | 23,441 (5.72e+03) |
| LTIM P=1 | 8,019,978 (3.06e+04) | 321,374 (1.96e+04) | 414,707 (2.53e+04) | 1,203,693 (2.94e+05) | 34,916 (3.41e+04) | 126,331 (3.08e+04) |
| LTIM P=2 | 6,567,972 (2.51e+04) | 287,073 (1.75e+04) | 337,180 (2.06e+04) | 989,970 (2.42e+05) | 22,205 (2.17e+04) | 112,357 (2.74e+04) |
| LTIM P=3..15 | 8,588,597 (3.28e+04) | 362,948 (2.22e+04) | 433,642 (2.65e+04) | 1,287,255 (3.14e+05) | 31,753 (3.1e+04) | 143,792 (3.51e+04) |
| LTIM P=16..127 | 2,565,605 (9.79e+03) | 105,032 (6.41e+03) | 140,362 (8.57e+03) | 428,420 (1.05e+05) | 6,136 (5.99e+03) | 35,726 (8.72e+03) |
| LTIM P=128..253 | 638,839 (2.44e+03) | 35,958 (2.19e+03) | 31,991 (1.95e+03) | 107,757 (2.63e+04) | 857 (837) | 9,413 (2.3e+03) |
| LTIM P=254 | 528,686 (2.02e+03) | 24,256 (1.48e+03) | 30,355 (1.85e+03) | 75,588 (1.85e+04) | 1,538 (1.5e+03) | 7,717 (1.88e+03) |
| LTIM P=255 | 497,011 (1.9e+03) | 21,054 (1.29e+03) | 23,989 (1.46e+03) | 79,531 (1.94e+04) | 776 (758) | 5,626 (1.37e+03) |
| LTIM Q=0 | 13,092,788 (4.99e+04) | 572,473 (3.49e+04) | 674,706 (4.12e+04) | 1,983,356 (4.84e+05) | 48,369 (4.72e+04) | 220,595 (5.39e+04) |
| LTIM Q=1 | 799,589 (3.05e+03) | 30,431 (1.86e+03) | 37,464 (2.29e+03) | 113,831 (2.78e+04) | 1,671 (1.63e+03) | 12,934 (3.16e+03) |
| LTIM Q=2..127 | 6,660,490 (2.54e+04) | 279,564 (1.71e+04) | 330,074 (2.01e+04) | 1,028,770 (2.51e+05) | 28,492 (2.78e+04) | 105,550 (2.58e+04) |
| LTIM Q=128 | 787,222 (3e+03) | 32,156 (1.96e+03) | 34,792 (2.12e+03) | 128,557 (3.14e+04) | 1,758 (1.72e+03) | 12,211 (2.98e+03) |
| LTIM Q=129..254 | 6,547,336 (2.5e+04) | 264,971 (1.62e+04) | 354,054 (2.16e+04) | 958,309 (2.34e+05) | 20,852 (2.04e+04) | 100,625 (2.46e+04) |
| LTIM Q=255 | 775,759 (2.96e+03) | 27,928 (1.7e+03) | 37,522 (2.29e+03) | 118,548 (2.89e+04) | 2,901 (2.83e+03) | 12,488 (3.05e+03) |
| LTIM D=0 (first tick after P) | 14,296,486 (5.45e+04) | 587,672 (3.59e+04) | 735,418 (4.49e+04) | 2,163,624 (5.28e+05) | 57,865 (5.65e+04) | 223,499 (5.46e+04) |
| LTIM D=1 | 2,657,248 (1.01e+04) | 107,696 (6.57e+03) | 135,574 (8.27e+03) | 422,483 (1.03e+05) | 8,055 (7.87e+03) | 49,145 (1.2e+04) |
| LTIM D=2..15 | 6,707,004 (2.56e+04) | 283,757 (1.73e+04) | 339,147 (2.07e+04) | 1,003,687 (2.45e+05) | 17,575 (1.72e+04) | 107,523 (2.63e+04) |
| LTIM D=16..254 | 3,726,294 (1.42e+04) | 165,150 (1.01e+04) | 193,689 (1.18e+04) | 540,643 (1.32e+05) | 16,669 (1.63e+04) | 66,291 (1.62e+04) |
| LTIM D=255 | 1,276,152 (4.87e+03) | 63,248 (3.86e+03) | 64,784 (3.95e+03) | 200,934 (4.91e+04) | 3,879 (3.79e+03) | 17,945 (4.38e+03) |
| LTIM P=255 with Q=0 (valid) | 497,011 (1.9e+03) | 21,054 (1.29e+03) | 23,989 (1.46e+03) | 79,531 (1.94e+04) | 776 (758) | 5,626 (1.37e+03) |
| LTIM restarts a running ticker | 19,623,943 (7.49e+04) | 723,925 (4.42e+04) | 942,788 (5.75e+04) | 3,195,924 (7.8e+05) | 65,848 (6.43e+04) | 316,659 (7.73e+04) |
| LTIM P=0 stops a running ticker | 612,945 (2.34e+03) | 21,018 (1.28e+03) | 24,918 (1.52e+03) | 88,333 (2.16e+04) | 4,320 (4.22e+03) | 11,642 (2.84e+03) |
| LTIM invalid: P=255 with Q!=0 | 22,155 (84.5) | 1,464 (89.4) | 7,884 (481) | 2,463 (601) | 115 (112) | 318 (77.6) |
| tick: bit boundary | 397,691,830 (1.52e+06) | 24,619,519 (1.5e+06) | 16,424,870 (1e+06) | 53,021,803 (1.29e+07) | 1,566,104 (1.53e+06) | 6,306,404 (1.54e+06) |
| tick: mid-bit | 388,553,443 (1.48e+06) | 24,226,099 (1.48e+06) | 15,936,676 (9.73e+05) | 51,666,169 (1.26e+07) | 1,530,618 (1.49e+06) | 6,145,720 (1.5e+06) |
| tick with fraction carry (P+1 cycles) | 163,168,362 (6.22e+05) | 10,279,105 (6.27e+05) | 6,664,438 (4.07e+05) | 21,670,549 (5.29e+06) | 636,871 (6.22e+05) | 2,605,773 (6.36e+05) |
| tick with fraction carry at P=254 (255 cycles) | 55,727 (213) | 3,986 (243) | 2,455 (150) | 6,595 (1.61e+03) | 269 (263) | 813 (198) |
| ticks in consecutive cycles (P=1) | 346,755,695 (1.32e+06) | 20,933,639 (1.28e+06) | 14,315,535 (8.74e+05) | 46,459,579 (1.13e+07) | 1,390,380 (1.36e+06) | 5,527,740 (1.35e+06) |
| ticker runs during WAIT | 8,259,563 (3.15e+04) | 161,852 (9.88e+03) | 341,414 (2.08e+04) | 1,072,830 (2.62e+05) | 29,207 (2.85e+04) | 137,431 (3.36e+04) |
| ticker runs while stalled on PULL | 157,677,960 (6.01e+05) | 10,471,560 (6.39e+05) | 6,072,846 (3.71e+05) | 18,101,567 (4.42e+06) | 648,575 (6.33e+05) | 2,481,239 (6.06e+05) |
| ticker runs while stalled on PUSH | 148,009,967 (5.65e+05) | 6,964,628 (4.25e+05) | 5,105,621 (3.12e+05) | 24,275,145 (5.93e+06) | 537,665 (5.25e+05) | 2,366,430 (5.78e+05) |
| ticker runs while stalled on WAITPIN | 46,406,739 (1.77e+05) | 2,457,148 (1.5e+05) | 1,788,152 (1.09e+05) | 5,256,006 (1.28e+06) | 177,492 (1.73e+05) | 745,295 (1.82e+05) |
| ticker runs while stalled on WAITEVENT | 18,581,643 (7.09e+04) | 466,055 (2.84e+04) | 734,051 (4.48e+04) | 1,654,327 (4.04e+05) | 72,377 (7.07e+04) | 275,917 (6.74e+04) |
| classic XFER stops a running ticker | 2,621,629 (1e+04) | 114,289 (6.98e+03) | 103,893 (6.34e+03) | 343,330 (8.38e+04) | 10,129 (9.89e+03) | 42,167 (1.03e+04) |
| LCFG line code NRZ | 8,105,438 (3.09e+04) | 364,714 (2.23e+04) | 406,488 (2.48e+04) | 1,267,812 (3.1e+05) | 31,489 (3.08e+04) | 122,905 (3e+04) |
| LCFG line code NRZI | 12,437,356 (4.74e+04) | 511,233 (3.12e+04) | 686,519 (4.19e+04) | 1,974,143 (4.82e+05) | 42,818 (4.18e+04) | 180,624 (4.41e+04) |
| LCFG line code Manchester | 7,315,523 (2.79e+04) | 323,151 (1.97e+04) | 366,728 (2.24e+04) | 1,134,140 (2.77e+05) | 31,773 (3.1e+04) | 113,485 (2.77e+04) |
| LCFG stuffing, runs of 1s, length 1 | 713,342 (2.72e+03) | 28,301 (1.73e+03) | 47,546 (2.9e+03) | 114,589 (2.8e+04) | 1,543 (1.51e+03) | 8,467 (2.07e+03) |
| LCFG stuffing, runs of 1s, length 2 | 1,239,000 (4.73e+03) | 55,269 (3.37e+03) | 72,266 (4.41e+03) | 187,182 (4.57e+04) | 4,904 (4.79e+03) | 17,747 (4.33e+03) |
| LCFG stuffing, runs of 1s, length 3 | 1,238,436 (4.72e+03) | 53,655 (3.27e+03) | 67,136 (4.1e+03) | 178,921 (4.37e+04) | 4,714 (4.6e+03) | 15,011 (3.66e+03) |
| LCFG stuffing, runs of 1s, length 4 | 1,256,193 (4.79e+03) | 55,910 (3.41e+03) | 70,112 (4.28e+03) | 199,986 (4.88e+04) | 5,934 (5.79e+03) | 15,392 (3.76e+03) |
| LCFG stuffing, runs of 1s, length 5 | 1,269,113 (4.84e+03) | 53,916 (3.29e+03) | 57,507 (3.51e+03) | 193,970 (4.74e+04) | 6,134 (5.99e+03) | 19,237 (4.7e+03) |
| LCFG stuffing, runs of 1s, length 6 | 1,270,061 (4.84e+03) | 48,940 (2.99e+03) | 63,968 (3.9e+03) | 183,025 (4.47e+04) | 4,795 (4.68e+03) | 14,663 (3.58e+03) |
| LCFG stuffing, runs of 1s, length 7 | 738,657 (2.82e+03) | 30,824 (1.88e+03) | 29,115 (1.78e+03) | 125,012 (3.05e+04) | 4,875 (4.76e+03) | 10,872 (2.65e+03) |
| LCFG stuffing, runs of 1s, length 8 | 755,307 (2.88e+03) | 30,653 (1.87e+03) | 39,886 (2.43e+03) | 116,420 (2.84e+04) | 1,819 (1.78e+03) | 8,381 (2.05e+03) |
| LCFG stuffing, runs of either polarity, length 2 | 1,209,652 (4.61e+03) | 56,113 (3.42e+03) | 60,258 (3.68e+03) | 198,521 (4.85e+04) | 4,758 (4.65e+03) | 10,813 (2.64e+03) |
| LCFG stuffing, runs of either polarity, length 3 | 1,205,410 (4.6e+03) | 51,787 (3.16e+03) | 71,711 (4.38e+03) | 178,741 (4.36e+04) | 6,737 (6.58e+03) | 19,759 (4.82e+03) |
| LCFG stuffing, runs of either polarity, length 4 | 1,159,210 (4.42e+03) | 55,772 (3.4e+03) | 75,308 (4.6e+03) | 208,020 (5.08e+04) | 3,931 (3.84e+03) | 26,199 (6.4e+03) |
| LCFG stuffing, runs of either polarity, length 5 | 1,243,100 (4.74e+03) | 53,167 (3.25e+03) | 69,431 (4.24e+03) | 196,884 (4.81e+04) | 4,141 (4.04e+03) | 23,015 (5.62e+03) |
| LCFG stuffing, runs of either polarity, length 6 | 1,153,072 (4.4e+03) | 51,171 (3.12e+03) | 63,510 (3.88e+03) | 201,041 (4.91e+04) | 6,075 (5.93e+03) | 19,084 (4.66e+03) |
| LCFG stuffing, runs of either polarity, length 7 | 707,522 (2.7e+03) | 28,068 (1.71e+03) | 44,419 (2.71e+03) | 104,806 (2.56e+04) | 2,728 (2.66e+03) | 9,985 (2.44e+03) |
| LCFG stuffing, runs of either polarity, length 8 | 706,263 (2.69e+03) | 27,753 (1.69e+03) | 34,421 (2.1e+03) | 108,021 (2.64e+04) | 2,011 (1.96e+03) | 9,766 (2.38e+03) |
| LCFG pair | 11,546,155 (4.4e+04) | 502,239 (3.07e+04) | 603,604 (3.68e+04) | 1,802,517 (4.4e+05) | 43,945 (4.29e+04) | 157,359 (3.84e+04) |
| LCFG arbitration monitor | 8,558,723 (3.26e+04) | 362,060 (2.21e+04) | 428,587 (2.62e+04) | 1,361,730 (3.32e+05) | 37,583 (3.67e+04) | 121,828 (2.97e+04) |
| LCFG SE0 end | 8,698,417 (3.32e+04) | 372,394 (2.27e+04) | 437,208 (2.67e+04) | 1,378,784 (3.37e+05) | 41,424 (4.05e+04) | 134,458 (3.28e+04) |
| LCFG initial level 0 | 14,056,925 (5.36e+04) | 591,224 (3.61e+04) | 726,339 (4.43e+04) | 2,208,774 (5.39e+05) | 51,860 (5.06e+04) | 199,108 (4.86e+04) |
| LCFG initial level 1 | 13,801,392 (5.26e+04) | 607,874 (3.71e+04) | 733,396 (4.48e+04) | 2,167,321 (5.29e+05) | 54,220 (5.29e+04) | 217,906 (5.32e+04) |
| LCFG polarity bit without stuffing (ignored) | 767,749 (2.93e+03) | 32,735 (2e+03) | 39,177 (2.39e+03) | 126,097 (3.08e+04) | 1,772 (1.73e+03) | 14,476 (3.53e+03) |
| LCFG clears set flags (SE0, lost, stuff error) | 546,039 (2.08e+03) | 37,609 (2.3e+03) | 18,635 (1.14e+03) | 65,450 (1.6e+04) | 2,242 (2.19e+03) | 7,717 (1.88e+03) |
| LCFG invalid: line code 3 | 33,197 (127) | 2,208 (135) | 11,933 (728) | 3,542 (865) | 117 (114) | 567 (138) |
| LCFG invalid: bits 23..11 set | 21,721 (82.9) | 1,545 (94.3) | 7,810 (477) | 2,321 (567) | 56 (54.7) | 397 (96.9) |
| LCFG invalid: stuffing on either polarity with run length 1 | 23,296 (88.9) | 1,735 (106) | 8,126 (496) | 2,580 (630) | 93 (90.8) | 335 (81.8) |
| CRC set from r0 | 804,324 (3.07e+03) | 25,349 (1.55e+03) | 34,819 (2.13e+03) | 113,404 (2.77e+04) | 2,050 (2e+03) | 13,384 (3.27e+03) |
| CRC set from r1 | 773,774 (2.95e+03) | 33,104 (2.02e+03) | 37,266 (2.27e+03) | 119,113 (2.91e+04) | 4,619 (4.51e+03) | 11,794 (2.88e+03) |
| CRC set from r2 | 14,702,682 (5.61e+04) | 650,073 (3.97e+04) | 815,742 (4.98e+04) | 2,209,144 (5.39e+05) | 54,642 (5.34e+04) | 249,660 (6.1e+04) |
| CRC set from r3 | 817,284 (3.12e+03) | 31,776 (1.94e+03) | 40,404 (2.47e+03) | 130,455 (3.18e+04) | 1,618 (1.58e+03) | 13,630 (3.33e+03) |
| CRC read into r0 | 1,475,980 (5.63e+03) | 68,362 (4.17e+03) | 73,851 (4.51e+03) | 246,778 (6.02e+04) | 3,130 (3.06e+03) | 19,794 (4.83e+03) |
| CRC read into r1 | 2,658,463 (1.01e+04) | 152,240 (9.29e+03) | 114,872 (7.01e+03) | 373,469 (9.12e+04) | 9,040 (8.83e+03) | 47,759 (1.17e+04) |
| CRC read into r2 | 2,871,823 (1.1e+04) | 163,500 (9.98e+03) | 119,375 (7.29e+03) | 416,104 (1.02e+05) | 11,236 (1.1e+04) | 36,590 (8.93e+03) |
| CRC read into r3 | 2,916,272 (1.11e+04) | 149,736 (9.14e+03) | 130,340 (7.96e+03) | 393,620 (9.61e+04) | 12,549 (1.23e+04) | 39,790 (9.71e+03) |
| CRC preset 0 selected | 5,870,854 (2.24e+04) | 262,026 (1.6e+04) | 335,050 (2.04e+04) | 882,989 (2.16e+05) | 20,911 (2.04e+04) | 102,228 (2.5e+04) |
| CRC preset 1 selected | 5,790,014 (2.21e+04) | 251,610 (1.54e+04) | 319,365 (1.95e+04) | 894,523 (2.18e+05) | 22,331 (2.18e+04) | 91,812 (2.24e+04) |
| CRC preset 2 selected | 5,787,817 (2.21e+04) | 285,627 (1.74e+04) | 317,773 (1.94e+04) | 954,107 (2.33e+05) | 22,171 (2.17e+04) | 98,885 (2.41e+04) |
| CRC preset 3 selected | 5,686,306 (2.17e+04) | 242,655 (1.48e+04) | 322,293 (1.97e+04) | 964,513 (2.35e+05) | 22,542 (2.2e+04) | 83,390 (2.04e+04) |
| CRC invalid: c=0 | 22,877 (87.3) | 1,383 (84.4) | 7,955 (486) | 2,362 (577) | 119 (116) | 346 (84.5) |
| CRC invalid: c>3 | 22,095 (84.3) | 1,587 (96.9) | 8,006 (489) | 2,413 (589) | 113 (110) | 350 (85.4) |
| CRC invalid: set with a!=0 | 22,098 (84.3) | 1,383 (84.4) | 8,109 (495) | 2,402 (586) | 116 (113) | 333 (81.3) |
| CRC invalid: set with b>3 | 21,927 (83.6) | 1,447 (88.3) | 8,056 (492) | 2,628 (642) | 52 (50.8) | 386 (94.2) |
| CRC invalid: read with a>3 | 22,315 (85.1) | 1,404 (85.7) | 7,530 (460) | 2,256 (551) | 97 (94.7) | 311 (75.9) |
| CRC invalid: read with b!=0 | 22,143 (84.5) | 1,516 (92.5) | 7,673 (468) | 2,535 (619) | 87 (85) | 378 (92.3) |
| CRC invalid: preset with a!=0 | 21,888 (83.5) | 1,542 (94.1) | 8,003 (488) | 2,522 (616) | 85 (83) | 257 (62.7) |
| CRC invalid: preset with b>3 | 22,227 (84.8) | 1,440 (87.9) | 7,779 (475) | 2,511 (613) | 65 (63.5) | 268 (65.4) |
| CRC fed: preset 0, LSB first, by line drive | 3,953,988 (1.51e+04) | 301,000 (1.84e+04) | 162,013 (9.89e+03) | 499,393 (1.22e+05) | 15,670 (1.53e+04) | 64,514 (1.58e+04) |
| CRC fed: preset 0, LSB first, by line sample | 3,476,612 (1.33e+04) | 287,821 (1.76e+04) | 141,645 (8.65e+03) | 397,226 (9.7e+04) | 13,854 (1.35e+04) | 53,583 (1.31e+04) |
| CRC fed: preset 0, LSB first, by classic drive | 201,333 (768) | 7,951 (485) | 7,636 (466) | 22,134 (5.4e+03) | 598 (584) | 3,593 (877) |
| CRC fed: preset 0, LSB first, by classic sample | 372,357 (1.42e+03) | 22,082 (1.35e+03) | 14,901 (909) | 46,559 (1.14e+04) | 586 (572) | 3,931 (960) |
| CRC fed: preset 0, MSB first, by line drive | 3,914,755 (1.49e+04) | 293,424 (1.79e+04) | 146,489 (8.94e+03) | 469,346 (1.15e+05) | 16,620 (1.62e+04) | 71,727 (1.75e+04) |
| CRC fed: preset 0, MSB first, by line sample | 3,464,621 (1.32e+04) | 286,204 (1.75e+04) | 148,663 (9.07e+03) | 435,715 (1.06e+05) | 14,488 (1.41e+04) | 56,433 (1.38e+04) |
| CRC fed: preset 0, MSB first, by classic drive | 199,870 (762) | 7,782 (475) | 8,698 (531) | 23,775 (5.8e+03) | 371 (362) | 3,112 (760) |
| CRC fed: preset 0, MSB first, by classic sample | 368,463 (1.41e+03) | 18,365 (1.12e+03) | 15,185 (927) | 50,142 (1.22e+04) | 1,084 (1.06e+03) | 11,289 (2.76e+03) |
| CRC fed: preset 1, LSB first, by line drive | 3,846,525 (1.47e+04) | 283,244 (1.73e+04) | 164,923 (1.01e+04) | 475,450 (1.16e+05) | 15,241 (1.49e+04) | 60,146 (1.47e+04) |
| CRC fed: preset 1, LSB first, by line sample | 3,460,465 (1.32e+04) | 293,848 (1.79e+04) | 157,322 (9.6e+03) | 420,065 (1.03e+05) | 11,766 (1.15e+04) | 54,828 (1.34e+04) |
| CRC fed: preset 1, LSB first, by classic drive | 198,654 (758) | 9,670 (590) | 8,819 (538) | 22,433 (5.48e+03) | 347 (339) | 2,789 (681) |
| CRC fed: preset 1, LSB first, by classic sample | 356,227 (1.36e+03) | 18,890 (1.15e+03) | 16,389 (1e+03) | 49,159 (1.2e+04) | 1,017 (993) | 4,618 (1.13e+03) |
| CRC fed: preset 1, MSB first, by line drive | 3,911,507 (1.49e+04) | 297,292 (1.81e+04) | 159,443 (9.73e+03) | 483,277 (1.18e+05) | 16,696 (1.63e+04) | 61,660 (1.51e+04) |
| CRC fed: preset 1, MSB first, by line sample | 3,426,477 (1.31e+04) | 286,663 (1.75e+04) | 147,486 (9e+03) | 417,061 (1.02e+05) | 11,904 (1.16e+04) | 48,911 (1.19e+04) |
| CRC fed: preset 1, MSB first, by classic drive | 185,242 (707) | 7,570 (462) | 8,056 (492) | 27,641 (6.75e+03) | 711 (694) | 3,988 (974) |
| CRC fed: preset 1, MSB first, by classic sample | 391,391 (1.49e+03) | 17,160 (1.05e+03) | 12,642 (772) | 44,327 (1.08e+04) | 1,037 (1.01e+03) | 4,608 (1.12e+03) |
| CRC fed: preset 2, LSB first, by line drive | 3,884,586 (1.48e+04) | 295,390 (1.8e+04) | 153,875 (9.39e+03) | 474,678 (1.16e+05) | 19,010 (1.86e+04) | 58,553 (1.43e+04) |
| CRC fed: preset 2, LSB first, by line sample | 3,487,466 (1.33e+04) | 283,633 (1.73e+04) | 147,582 (9.01e+03) | 418,016 (1.02e+05) | 16,284 (1.59e+04) | 55,772 (1.36e+04) |
| CRC fed: preset 2, LSB first, by classic drive | 202,830 (774) | 8,524 (520) | 7,378 (450) | 31,741 (7.75e+03) | 1,092 (1.07e+03) | 2,616 (639) |
| CRC fed: preset 2, LSB first, by classic sample | 389,313 (1.49e+03) | 16,364 (999) | 10,687 (652) | 44,648 (1.09e+04) | 1,509 (1.47e+03) | 5,554 (1.36e+03) |
| CRC fed: preset 2, MSB first, by line drive | 3,855,829 (1.47e+04) | 311,291 (1.9e+04) | 159,296 (9.72e+03) | 437,713 (1.07e+05) | 16,648 (1.63e+04) | 57,458 (1.4e+04) |
| CRC fed: preset 2, MSB first, by line sample | 3,429,946 (1.31e+04) | 281,068 (1.72e+04) | 149,748 (9.14e+03) | 404,235 (9.87e+04) | 12,311 (1.2e+04) | 53,547 (1.31e+04) |
| CRC fed: preset 2, MSB first, by classic drive | 188,812 (720) | 13,963 (852) | 5,935 (362) | 23,899 (5.83e+03) | 682 (666) | 2,119 (517) |
| CRC fed: preset 2, MSB first, by classic sample | 362,260 (1.38e+03) | 14,647 (894) | 18,459 (1.13e+03) | 54,037 (1.32e+04) | 1,275 (1.25e+03) | 6,726 (1.64e+03) |
| CRC fed: preset 3, LSB first, by line drive | 3,844,437 (1.47e+04) | 297,886 (1.82e+04) | 157,602 (9.62e+03) | 486,160 (1.19e+05) | 15,912 (1.55e+04) | 54,908 (1.34e+04) |
| CRC fed: preset 3, LSB first, by line sample | 3,418,587 (1.3e+04) | 271,670 (1.66e+04) | 152,701 (9.32e+03) | 418,210 (1.02e+05) | 14,386 (1.4e+04) | 51,403 (1.25e+04) |
| CRC fed: preset 3, LSB first, by classic drive | 223,762 (854) | 8,638 (527) | 7,898 (482) | 34,922 (8.53e+03) | 572 (559) | 1,869 (456) |
| CRC fed: preset 3, LSB first, by classic sample | 378,860 (1.45e+03) | 14,722 (899) | 17,343 (1.06e+03) | 53,799 (1.31e+04) | 1,216 (1.19e+03) | 3,998 (976) |
| CRC fed: preset 3, MSB first, by line drive | 3,877,888 (1.48e+04) | 293,781 (1.79e+04) | 152,203 (9.29e+03) | 483,336 (1.18e+05) | 12,441 (1.21e+04) | 60,143 (1.47e+04) |
| CRC fed: preset 3, MSB first, by line sample | 3,454,885 (1.32e+04) | 280,968 (1.71e+04) | 140,322 (8.56e+03) | 415,177 (1.01e+05) | 12,427 (1.21e+04) | 51,904 (1.27e+04) |
| CRC fed: preset 3, MSB first, by classic drive | 216,237 (825) | 6,216 (379) | 9,273 (566) | 33,250 (8.12e+03) | 246 (240) | 3,405 (831) |
| CRC fed: preset 3, MSB first, by classic sample | 374,702 (1.43e+03) | 22,516 (1.37e+03) | 18,861 (1.15e+03) | 58,728 (1.43e+04) | 599 (585) | 4,522 (1.1e+03) |
| LSTAT into r0 | 1,991,667 (7.6e+03) | 76,581 (4.67e+03) | 83,320 (5.09e+03) | 304,741 (7.44e+04) | 11,002 (1.07e+04) | 31,704 (7.74e+03) |
| LSTAT into r1 | 2,744,571 (1.05e+04) | 137,117 (8.37e+03) | 126,775 (7.74e+03) | 437,602 (1.07e+05) | 9,935 (9.7e+03) | 49,167 (1.2e+04) |
| LSTAT into r2 | 2,132,913 (8.14e+03) | 85,493 (5.22e+03) | 96,970 (5.92e+03) | 324,030 (7.91e+04) | 6,983 (6.82e+03) | 42,465 (1.04e+04) |
| LSTAT into r3 | 2,045,107 (7.8e+03) | 86,281 (5.27e+03) | 94,017 (5.74e+03) | 331,559 (8.09e+04) | 8,100 (7.91e+03) | 45,598 (1.11e+04) |
| LSTAT invalid: a>3 | 21,870 (83.4) | 1,435 (87.6) | 7,598 (464) | 2,554 (624) | 92 (89.8) | 289 (70.6) |
| LSTAT invalid: b!=0 | 21,967 (83.8) | 1,456 (88.9) | 7,918 (483) | 2,506 (612) | 82 (80.1) | 397 (96.9) |
| LSTAT invalid: c!=0 | 21,635 (82.5) | 1,450 (88.5) | 7,441 (454) | 2,686 (656) | 87 (85) | 338 (82.5) |
| LSTAT reads SE0=0 | 8,797,267 (3.36e+04) | 373,455 (2.28e+04) | 396,983 (2.42e+04) | 1,382,275 (3.37e+05) | 35,785 (3.49e+04) | 167,401 (4.09e+04) |
| LSTAT reads SE0=1 | 116,991 (446) | 12,017 (733) | 4,099 (250) | 15,657 (3.82e+03) | 235 (229) | 1,533 (374) |
| LSTAT reads arbitration lost=0 | 8,800,045 (3.36e+04) | 378,219 (2.31e+04) | 397,222 (2.42e+04) | 1,379,148 (3.37e+05) | 35,799 (3.5e+04) | 166,480 (4.06e+04) |
| LSTAT reads arbitration lost=1 | 114,213 (436) | 7,253 (443) | 3,860 (236) | 18,784 (4.59e+03) | 221 (216) | 2,454 (599) |
| LSTAT reads stuff error=0 | 8,367,830 (3.19e+04) | 348,021 (2.12e+04) | 381,178 (2.33e+04) | 1,320,425 (3.22e+05) | 33,264 (3.25e+04) | 162,270 (3.96e+04) |
| LSTAT reads stuff error=1 | 546,428 (2.08e+03) | 37,451 (2.29e+03) | 19,904 (1.21e+03) | 77,507 (1.89e+04) | 2,756 (2.69e+03) | 6,664 (1.63e+03) |
| LSTAT reads TX data=0 | 572,560 (2.18e+03) | 27,764 (1.69e+03) | 21,325 (1.3e+03) | 48,927 (1.19e+04) | 2,298 (2.24e+03) | 9,665 (2.36e+03) |
| LSTAT reads TX data=1 | 8,341,698 (3.18e+04) | 357,708 (2.18e+04) | 379,757 (2.32e+04) | 1,349,005 (3.29e+05) | 33,722 (3.29e+04) | 159,269 (3.89e+04) |
| LSTAT reads RX space=0 | 418,301 (1.6e+03) | 19,796 (1.21e+03) | 14,493 (885) | 76,083 (1.86e+04) | 1,485 (1.45e+03) | 8,095 (1.98e+03) |
| LSTAT reads RX space=1 | 8,495,957 (3.24e+04) | 365,676 (2.23e+04) | 386,589 (2.36e+04) | 1,321,849 (3.23e+05) | 34,535 (3.37e+04) | 160,839 (3.93e+04) |
| LSTAT reads line level=0 | 4,558,067 (1.74e+04) | 199,799 (1.22e+04) | 200,152 (1.22e+04) | 679,386 (1.66e+05) | 19,474 (1.9e+04) | 84,896 (2.07e+04) |
| LSTAT reads line level=1 | 4,356,191 (1.66e+04) | 185,673 (1.13e+04) | 200,930 (1.23e+04) | 718,546 (1.75e+05) | 16,546 (1.62e+04) | 84,038 (2.05e+04) |
| LSTAT reads ticker running=0 | 268,838 (1.03e+03) | 7,106 (434) | 9,021 (551) | 32,611 (7.96e+03) | 2,628 (2.57e+03) | 2,923 (714) |
| LSTAT reads ticker running=1 | 8,645,420 (3.3e+04) | 378,366 (2.31e+04) | 392,061 (2.39e+04) | 1,365,321 (3.33e+05) | 33,392 (3.26e+04) | 166,011 (4.05e+04) |
| LSTAT reads data bits left at SE0 > 0 | 116,991 (446) | 12,017 (733) | 4,099 (250) | 15,657 (3.82e+03) | 235 (229) | 1,533 (374) |
| line XFER started: drive, 1 bits | 695,096 (2.65e+03) | 42,381 (2.59e+03) | 33,752 (2.06e+03) | 108,508 (2.65e+04) | 2,529 (2.47e+03) | 12,282 (3e+03) |
| line XFER started: drive, 2 bits | 527,990 (2.01e+03) | 34,996 (2.14e+03) | 23,657 (1.44e+03) | 72,442 (1.77e+04) | 2,340 (2.29e+03) | 9,094 (2.22e+03) |
| line XFER started: drive, 3 bits | 275,638 (1.05e+03) | 19,288 (1.18e+03) | 10,772 (657) | 38,443 (9.39e+03) | 2,048 (2e+03) | 3,996 (976) |
| line XFER started: drive, 4 bits | 192,603 (735) | 13,605 (830) | 8,384 (512) | 22,514 (5.5e+03) | 632 (617) | 2,982 (728) |
| line XFER started: drive, 5 bits | 178,645 (681) | 12,444 (760) | 8,494 (518) | 21,330 (5.21e+03) | 555 (542) | 2,345 (573) |
| line XFER started: drive, 6 bits | 162,018 (618) | 11,984 (731) | 6,680 (408) | 20,740 (5.06e+03) | 596 (582) | 2,627 (641) |
| line XFER started: drive, 7 bits | 160,396 (612) | 11,201 (684) | 7,216 (440) | 20,658 (5.04e+03) | 564 (551) | 2,908 (710) |
| line XFER started: drive, 8 bits | 1,105,119 (4.22e+03) | 79,259 (4.84e+03) | 46,059 (2.81e+03) | 138,712 (3.39e+04) | 4,559 (4.45e+03) | 18,148 (4.43e+03) |
| line XFER started: drive, 9 bits | 149,414 (570) | 11,372 (694) | 5,150 (314) | 18,612 (4.54e+03) | 345 (337) | 2,407 (588) |
| line XFER started: drive, 10 bits | 142,578 (544) | 10,249 (626) | 6,490 (396) | 17,922 (4.38e+03) | 409 (399) | 2,282 (557) |
| line XFER started: drive, 11 bits | 135,811 (518) | 10,475 (639) | 5,984 (365) | 16,560 (4.04e+03) | 417 (407) | 2,045 (499) |
| line XFER started: drive, 12 bits | 140,404 (536) | 10,021 (612) | 5,791 (353) | 19,321 (4.72e+03) | 453 (442) | 1,933 (472) |
| line XFER started: drive, 13 bits | 52,753 (201) | 3,877 (237) | 2,223 (136) | 6,279 (1.53e+03) | 157 (153) | 855 (209) |
| line XFER started: drive, 14 bits | 49,822 (190) | 4,121 (252) | 2,215 (135) | 6,173 (1.51e+03) | 182 (178) | 672 (164) |
| line XFER started: drive, 15 bits | 49,306 (188) | 3,765 (230) | 2,255 (138) | 5,553 (1.36e+03) | 228 (223) | 765 (187) |
| line XFER started: drive, 16 bits | 841,757 (3.21e+03) | 66,246 (4.04e+03) | 34,657 (2.12e+03) | 102,356 (2.5e+04) | 3,512 (3.43e+03) | 13,236 (3.23e+03) |
| line XFER started: drive, 17 bits | 46,065 (176) | 3,912 (239) | 2,192 (134) | 5,461 (1.33e+03) | 173 (169) | 785 (192) |
| line XFER started: drive, 18 bits | 45,270 (173) | 3,237 (198) | 1,955 (119) | 5,503 (1.34e+03) | 268 (262) | 605 (148) |
| line XFER started: drive, 19 bits | 45,417 (173) | 3,812 (233) | 2,226 (136) | 5,323 (1.3e+03) | 154 (150) | 724 (177) |
| line XFER started: drive, 20 bits | 45,193 (172) | 3,301 (201) | 2,163 (132) | 5,386 (1.31e+03) | 140 (137) | 552 (135) |
| line XFER started: drive, 21 bits | 43,263 (165) | 3,285 (201) | 2,001 (122) | 5,197 (1.27e+03) | 151 (147) | 694 (169) |
| line XFER started: drive, 22 bits | 42,739 (163) | 3,506 (214) | 1,583 (96.6) | 4,907 (1.2e+03) | 204 (199) | 650 (159) |
| line XFER started: drive, 23 bits | 41,983 (160) | 3,565 (218) | 1,802 (110) | 4,989 (1.22e+03) | 160 (156) | 677 (165) |
| line XFER started: drive, 24 bits | 41,213 (157) | 3,293 (201) | 1,812 (111) | 5,351 (1.31e+03) | 252 (246) | 672 (164) |
| line XFER started: drive, 25 bits | 40,703 (155) | 3,463 (211) | 1,657 (101) | 4,672 (1.14e+03) | 177 (173) | 594 (145) |
| line XFER started: drive, 26 bits | 40,944 (156) | 3,199 (195) | 1,838 (112) | 4,881 (1.19e+03) | 162 (158) | 620 (151) |
| line XFER started: drive, 27 bits | 40,467 (154) | 3,329 (203) | 1,744 (106) | 4,831 (1.18e+03) | 149 (146) | 651 (159) |
| line XFER started: drive, 28 bits | 39,469 (151) | 3,309 (202) | 1,685 (103) | 4,589 (1.12e+03) | 100 (97.7) | 655 (160) |
| line XFER started: drive, 29 bits | 38,968 (149) | 3,137 (191) | 1,810 (110) | 4,344 (1.06e+03) | 204 (199) | 559 (136) |
| line XFER started: drive, 30 bits | 38,723 (148) | 2,929 (179) | 1,520 (92.8) | 4,194 (1.02e+03) | 180 (176) | 501 (122) |
| line XFER started: drive, 31 bits | 240,054 (916) | 19,558 (1.19e+03) | 9,606 (586) | 28,128 (6.87e+03) | 964 (941) | 4,018 (981) |
| line XFER started: drive, 32 bits | 239,514 (914) | 19,352 (1.18e+03) | 9,701 (592) | 27,787 (6.78e+03) | 869 (849) | 3,978 (971) |
| line XFER started: sample, 1 bits | 353,918 (1.35e+03) | 26,337 (1.61e+03) | 15,964 (974) | 48,230 (1.18e+04) | 683 (667) | 5,253 (1.28e+03) |
| line XFER started: sample, 2 bits | 285,529 (1.09e+03) | 19,469 (1.19e+03) | 13,628 (832) | 37,903 (9.25e+03) | 1,076 (1.05e+03) | 5,448 (1.33e+03) |
| line XFER started: sample, 3 bits | 153,839 (587) | 12,042 (735) | 6,862 (419) | 21,007 (5.13e+03) | 779 (761) | 2,197 (536) |
| line XFER started: sample, 4 bits | 110,441 (421) | 8,061 (492) | 5,392 (329) | 15,306 (3.74e+03) | 532 (520) | 1,532 (374) |
| line XFER started: sample, 5 bits | 102,284 (390) | 8,123 (496) | 3,782 (231) | 12,085 (2.95e+03) | 375 (366) | 1,432 (350) |
| line XFER started: sample, 6 bits | 96,944 (370) | 7,000 (427) | 3,998 (244) | 13,155 (3.21e+03) | 356 (348) | 1,644 (401) |
| line XFER started: sample, 7 bits | 94,221 (359) | 7,098 (433) | 3,568 (218) | 12,141 (2.96e+03) | 267 (261) | 2,048 (500) |
| line XFER started: sample, 8 bits | 656,919 (2.51e+03) | 47,982 (2.93e+03) | 27,948 (1.71e+03) | 81,351 (1.99e+04) | 2,596 (2.54e+03) | 10,361 (2.53e+03) |
| line XFER started: sample, 9 bits | 86,629 (330) | 7,130 (435) | 4,211 (257) | 10,153 (2.48e+03) | 349 (341) | 1,330 (325) |
| line XFER started: sample, 10 bits | 85,547 (326) | 7,493 (457) | 3,569 (218) | 10,742 (2.62e+03) | 481 (470) | 1,208 (295) |
| line XFER started: sample, 11 bits | 81,554 (311) | 6,252 (382) | 4,155 (254) | 10,503 (2.56e+03) | 280 (273) | 990 (242) |
| line XFER started: sample, 12 bits | 86,713 (331) | 6,810 (416) | 3,401 (208) | 10,904 (2.66e+03) | 229 (224) | 1,539 (376) |
| line XFER started: sample, 13 bits | 33,833 (129) | 2,722 (166) | 1,459 (89.1) | 3,942 (962) | 101 (98.6) | 403 (98.4) |
| line XFER started: sample, 14 bits | 30,401 (116) | 2,333 (142) | 1,659 (101) | 3,856 (941) | 146 (143) | 533 (130) |
| line XFER started: sample, 15 bits | 31,233 (119) | 2,670 (163) | 1,255 (76.6) | 3,614 (882) | 181 (177) | 519 (127) |
| line XFER started: sample, 16 bits | 524,900 (2e+03) | 42,234 (2.58e+03) | 22,524 (1.37e+03) | 63,562 (1.55e+04) | 2,298 (2.24e+03) | 8,053 (1.97e+03) |
| line XFER started: sample, 17 bits | 27,917 (106) | 2,371 (145) | 1,415 (86.4) | 3,270 (798) | 99 (96.7) | 456 (111) |
| line XFER started: sample, 18 bits | 27,840 (106) | 2,354 (144) | 1,360 (83) | 3,790 (925) | 94 (91.8) | 404 (98.6) |
| line XFER started: sample, 19 bits | 25,941 (99) | 2,269 (138) | 984 (60.1) | 3,260 (796) | 73 (71.3) | 450 (110) |
| line XFER started: sample, 20 bits | 28,716 (110) | 2,096 (128) | 1,416 (86.4) | 3,180 (776) | 70 (68.4) | 516 (126) |
| line XFER started: sample, 21 bits | 26,135 (99.7) | 1,886 (115) | 963 (58.8) | 2,926 (714) | 82 (80.1) | 403 (98.4) |
| line XFER started: sample, 22 bits | 26,668 (102) | 2,211 (135) | 1,090 (66.5) | 2,934 (716) | 81 (79.1) | 497 (121) |
| line XFER started: sample, 23 bits | 25,862 (98.7) | 2,131 (130) | 1,075 (65.6) | 2,956 (722) | 109 (106) | 361 (88.1) |
| line XFER started: sample, 24 bits | 25,399 (96.9) | 1,919 (117) | 892 (54.4) | 3,070 (750) | 109 (106) | 451 (110) |
| line XFER started: sample, 25 bits | 26,272 (100) | 2,118 (129) | 1,095 (66.8) | 3,379 (825) | 80 (78.1) | 428 (104) |
| line XFER started: sample, 26 bits | 25,736 (98.2) | 2,234 (136) | 1,112 (67.9) | 2,598 (634) | 63 (61.5) | 351 (85.7) |
| line XFER started: sample, 27 bits | 25,145 (95.9) | 2,047 (125) | 1,301 (79.4) | 3,437 (839) | 79 (77.1) | 339 (82.8) |
| line XFER started: sample, 28 bits | 25,234 (96.3) | 1,992 (122) | 941 (57.4) | 2,810 (686) | 119 (116) | 381 (93) |
| line XFER started: sample, 29 bits | 27,774 (106) | 2,034 (124) | 1,010 (61.6) | 2,791 (681) | 64 (62.5) | 326 (79.6) |
| line XFER started: sample, 30 bits | 24,685 (94.2) | 2,197 (134) | 944 (57.6) | 2,930 (715) | 58 (56.6) | 347 (84.7) |
| line XFER started: sample, 31 bits | 155,050 (591) | 12,355 (754) | 6,128 (374) | 17,542 (4.28e+03) | 547 (534) | 2,355 (575) |
| line XFER started: sample, 32 bits | 151,409 (578) | 12,272 (749) | 7,231 (441) | 16,925 (4.13e+03) | 501 (489) | 2,012 (491) |
| line XFER started: drive+sample, 1 bits | 182,333 (696) | 13,657 (834) | 7,978 (487) | 27,887 (6.81e+03) | 1,024 (1e+03) | 2,629 (642) |
| line XFER started: drive+sample, 2 bits | 160,142 (611) | 11,798 (720) | 6,706 (409) | 21,541 (5.26e+03) | 462 (451) | 3,237 (790) |
| line XFER started: drive+sample, 3 bits | 91,443 (349) | 6,631 (405) | 4,540 (277) | 11,130 (2.72e+03) | 277 (271) | 1,199 (293) |
| line XFER started: drive+sample, 4 bits | 66,208 (253) | 5,098 (311) | 3,437 (210) | 7,720 (1.88e+03) | 247 (241) | 1,021 (249) |
| line XFER started: drive+sample, 5 bits | 59,586 (227) | 4,485 (274) | 2,282 (139) | 8,224 (2.01e+03) | 198 (193) | 794 (194) |
| line XFER started: drive+sample, 6 bits | 59,851 (228) | 4,566 (279) | 2,562 (156) | 7,200 (1.76e+03) | 108 (105) | 1,491 (364) |
| line XFER started: drive+sample, 7 bits | 55,066 (210) | 4,395 (268) | 3,273 (200) | 7,983 (1.95e+03) | 168 (164) | 989 (241) |
| line XFER started: drive+sample, 8 bits | 393,318 (1.5e+03) | 31,654 (1.93e+03) | 15,498 (946) | 46,050 (1.12e+04) | 1,744 (1.7e+03) | 6,234 (1.52e+03) |
| line XFER started: drive+sample, 9 bits | 53,733 (205) | 4,345 (265) | 2,284 (139) | 5,768 (1.41e+03) | 239 (233) | 612 (149) |
| line XFER started: drive+sample, 10 bits | 51,405 (196) | 3,951 (241) | 2,340 (143) | 6,279 (1.53e+03) | 171 (167) | 842 (206) |
| line XFER started: drive+sample, 11 bits | 51,445 (196) | 3,686 (225) | 2,550 (156) | 5,568 (1.36e+03) | 155 (151) | 763 (186) |
| line XFER started: drive+sample, 12 bits | 52,122 (199) | 4,475 (273) | 2,552 (156) | 6,296 (1.54e+03) | 118 (115) | 923 (225) |
| line XFER started: drive+sample, 13 bits | 20,153 (76.9) | 1,662 (101) | 952 (58.1) | 2,339 (571) | 53 (51.8) | 238 (58.1) |
| line XFER started: drive+sample, 14 bits | 19,078 (72.8) | 1,630 (99.5) | 941 (57.4) | 2,401 (586) | 40 (39.1) | 312 (76.2) |
| line XFER started: drive+sample, 15 bits | 18,691 (71.3) | 1,527 (93.2) | 935 (57.1) | 1,927 (470) | 59 (57.6) | 176 (43) |
| line XFER started: drive+sample, 16 bits | 313,771 (1.2e+03) | 25,917 (1.58e+03) | 12,938 (790) | 39,279 (9.59e+03) | 1,399 (1.37e+03) | 4,995 (1.22e+03) |
| line XFER started: drive+sample, 17 bits | 17,868 (68.2) | 1,372 (83.7) | 705 (43) | 2,005 (490) | 52 (50.8) | 224 (54.7) |
| line XFER started: drive+sample, 18 bits | 17,185 (65.6) | 1,369 (83.6) | 799 (48.8) | 2,398 (585) | 69 (67.4) | 270 (65.9) |
| line XFER started: drive+sample, 19 bits | 16,441 (62.7) | 1,413 (86.2) | 789 (48.2) | 2,213 (540) | 64 (62.5) | 272 (66.4) |
| line XFER started: drive+sample, 20 bits | 16,678 (63.6) | 1,570 (95.8) | 967 (59) | 2,633 (643) | 48 (46.9) | 334 (81.5) |
| line XFER started: drive+sample, 21 bits | 16,131 (61.5) | 1,411 (86.1) | 707 (43.2) | 1,711 (418) | 47 (45.9) | 201 (49.1) |
| line XFER started: drive+sample, 22 bits | 16,332 (62.3) | 1,466 (89.5) | 976 (59.6) | 1,831 (447) | 52 (50.8) | 267 (65.2) |
| line XFER started: drive+sample, 23 bits | 16,330 (62.3) | 1,268 (77.4) | 831 (50.7) | 1,839 (449) | 53 (51.8) | 247 (60.3) |
| line XFER started: drive+sample, 24 bits | 16,265 (62) | 1,420 (86.7) | 665 (40.6) | 2,313 (565) | 90 (87.9) | 203 (49.6) |
| line XFER started: drive+sample, 25 bits | 16,080 (61.3) | 1,468 (89.6) | 1,051 (64.1) | 1,823 (445) | 70 (68.4) | 208 (50.8) |
| line XFER started: drive+sample, 26 bits | 15,637 (59.7) | 1,328 (81.1) | 967 (59) | 2,107 (514) | 52 (50.8) | 370 (90.3) |
| line XFER started: drive+sample, 27 bits | 15,375 (58.7) | 1,455 (88.8) | 783 (47.8) | 2,073 (506) | 56 (54.7) | 188 (45.9) |
| line XFER started: drive+sample, 28 bits | 15,329 (58.5) | 1,340 (81.8) | 704 (43) | 2,185 (533) | 52 (50.8) | 263 (64.2) |
| line XFER started: drive+sample, 29 bits | 16,020 (61.1) | 1,340 (81.8) | 1,030 (62.9) | 1,651 (403) | 125 (122) | 193 (47.1) |
| line XFER started: drive+sample, 30 bits | 14,699 (56.1) | 1,358 (82.9) | 743 (45.3) | 1,721 (420) | 62 (60.5) | 238 (58.1) |
| line XFER started: drive+sample, 31 bits | 95,879 (366) | 7,537 (460) | 3,978 (243) | 11,300 (2.76e+03) | 357 (349) | 1,561 (381) |
| line XFER started: drive+sample, 32 bits | 93,952 (358) | 7,670 (468) | 3,817 (233) | 10,887 (2.66e+03) | 360 (352) | 1,208 (295) |
| line XFER completed: drive, 1 bit | 689,679 (2.63e+03) | 42,002 (2.56e+03) | 33,510 (2.05e+03) | 107,947 (2.64e+04) | 2,506 (2.45e+03) | 12,206 (2.98e+03) |
| line XFER completed: drive, 2..31 bits | 4,646,223 (1.77e+04) | 341,538 (2.08e+04) | 196,800 (1.2e+04) | 589,484 (1.44e+05) | 19,145 (1.87e+04) | 73,762 (1.8e+04) |
| line XFER completed: drive, 32 bits | 210,246 (802) | 16,831 (1.03e+03) | 8,495 (518) | 24,772 (6.05e+03) | 770 (752) | 3,522 (860) |
| line XFER completed: sample, 1 bit | 331,791 (1.27e+03) | 23,430 (1.43e+03) | 14,782 (902) | 45,217 (1.1e+04) | 659 (644) | 4,904 (1.2e+03) |
| line XFER completed: sample, 2..31 bits | 2,477,082 (9.45e+03) | 193,579 (1.18e+04) | 107,157 (6.54e+03) | 311,619 (7.61e+04) | 9,811 (9.58e+03) | 38,835 (9.48e+03) |
| line XFER completed: sample, 32 bits | 114,891 (438) | 9,745 (595) | 5,292 (323) | 13,468 (3.29e+03) | 390 (381) | 1,581 (386) |
| line XFER completed: drive+sample, 1 bit | 173,534 (662) | 13,116 (801) | 7,729 (472) | 26,403 (6.45e+03) | 1,011 (987) | 2,501 (611) |
| line XFER completed: drive+sample, 2..31 bits | 1,498,896 (5.72e+03) | 121,038 (7.39e+03) | 66,700 (4.07e+03) | 185,894 (4.54e+04) | 5,423 (5.3e+03) | 24,903 (6.08e+03) |
| line XFER completed: drive+sample, 32 bits | 72,767 (278) | 6,022 (368) | 2,883 (176) | 8,413 (2.05e+03) | 272 (266) | 944 (230) |
| line XFER drive, LSB first | 2,982,800 (1.14e+04) | 217,469 (1.33e+04) | 134,200 (8.19e+03) | 379,909 (9.28e+04) | 13,291 (1.3e+04) | 47,110 (1.15e+04) |
| line XFER drive, MSB first | 2,946,535 (1.12e+04) | 214,002 (1.31e+04) | 120,874 (7.38e+03) | 381,757 (9.32e+04) | 10,542 (1.03e+04) | 48,402 (1.18e+04) |
| line XFER sample, LSB first | 1,748,535 (6.67e+03) | 134,762 (8.23e+03) | 78,809 (4.81e+03) | 216,619 (5.29e+04) | 6,619 (6.46e+03) | 27,194 (6.64e+03) |
| line XFER sample, MSB first | 1,742,153 (6.65e+03) | 133,480 (8.15e+03) | 73,523 (4.49e+03) | 219,633 (5.36e+04) | 6,338 (6.19e+03) | 27,373 (6.68e+03) |
| line XFER drive+sample, LSB first | 1,035,039 (3.95e+03) | 81,647 (4.98e+03) | 44,798 (2.73e+03) | 122,532 (2.99e+04) | 4,088 (3.99e+03) | 15,935 (3.89e+03) |
| line XFER drive+sample, MSB first | 1,029,507 (3.93e+03) | 82,615 (5.04e+03) | 46,482 (2.84e+03) | 135,750 (3.31e+04) | 3,983 (3.89e+03) | 16,767 (4.09e+03) |
| line XFER drive, NRZ | 1,162,578 (4.43e+03) | 87,227 (5.32e+03) | 53,859 (3.29e+03) | 153,568 (3.75e+04) | 4,357 (4.25e+03) | 18,581 (4.54e+03) |
| line XFER drive, NRZI | 1,749,006 (6.67e+03) | 134,949 (8.24e+03) | 75,763 (4.62e+03) | 229,727 (5.61e+04) | 6,432 (6.28e+03) | 27,894 (6.81e+03) |
| line XFER drive, Manchester | 3,017,751 (1.15e+04) | 209,295 (1.28e+04) | 125,452 (7.66e+03) | 378,371 (9.24e+04) | 13,044 (1.27e+04) | 49,037 (1.2e+04) |
| line XFER sample, NRZ | 1,405,467 (5.36e+03) | 108,570 (6.63e+03) | 60,798 (3.71e+03) | 176,342 (4.31e+04) | 4,950 (4.83e+03) | 23,190 (5.66e+03) |
| line XFER sample, NRZI | 2,085,221 (7.95e+03) | 159,672 (9.75e+03) | 91,534 (5.59e+03) | 259,910 (6.35e+04) | 8,007 (7.82e+03) | 31,377 (7.66e+03) |
| line XFER drive+sample, NRZ | 827,421 (3.16e+03) | 65,294 (3.99e+03) | 34,323 (2.09e+03) | 102,478 (2.5e+04) | 2,969 (2.9e+03) | 12,806 (3.13e+03) |
| line XFER drive+sample, NRZI | 1,237,125 (4.72e+03) | 98,968 (6.04e+03) | 56,957 (3.48e+03) | 155,804 (3.8e+04) | 5,102 (4.98e+03) | 19,896 (4.86e+03) |
| line XFER drive with pair | 2,114,463 (8.07e+03) | 150,951 (9.21e+03) | 93,694 (5.72e+03) | 264,496 (6.46e+04) | 8,244 (8.05e+03) | 34,493 (8.42e+03) |
| line XFER sample with pair | 1,442,802 (5.5e+03) | 105,201 (6.42e+03) | 63,309 (3.86e+03) | 177,104 (4.32e+04) | 5,477 (5.35e+03) | 21,981 (5.37e+03) |
| line XFER drive+sample with pair | 717,376 (2.74e+03) | 55,388 (3.38e+03) | 30,442 (1.86e+03) | 89,461 (2.18e+04) | 2,742 (2.68e+03) | 10,456 (2.55e+03) |
| line XFER drive with CRC feed | 2,963,658 (1.13e+04) | 215,149 (1.31e+04) | 123,436 (7.53e+03) | 380,295 (9.28e+04) | 11,808 (1.15e+04) | 46,275 (1.13e+04) |
| line XFER sample with CRC feed | 1,745,920 (6.66e+03) | 132,170 (8.07e+03) | 75,270 (4.59e+03) | 215,396 (5.26e+04) | 6,153 (6.01e+03) | 27,535 (6.72e+03) |
| line XFER drive+sample with CRC feed | 1,038,145 (3.96e+03) | 83,103 (5.07e+03) | 45,087 (2.75e+03) | 128,204 (3.13e+04) | 4,256 (4.16e+03) | 16,487 (4.03e+03) |
| line XFER with neither drive nor sample | 38,723 (148) | 3,949 (241) | 1,479 (90.3) | 3,590 (876) | 144 (141) | 557 (136) |
| line XFER issued before the next boundary (no gap) | 2,787,284 (1.06e+04) | 247,678 (1.51e+04) | 127,166 (7.76e+03) | 372,924 (9.1e+04) | 10,298 (1.01e+04) | 44,508 (1.09e+04) |
| line XFER invalid: b!=0 | 34,122 (130) | 2,098 (128) | 11,977 (731) | 3,866 (944) | 165 (161) | 606 (148) |
| line XFER invalid: c[1:0]!=0 | 31,031 (118) | 2,140 (131) | 10,669 (651) | 3,671 (896) | 182 (178) | 504 (123) |
| line XFER invalid: a=0 | 22,271 (85) | 1,455 (88.8) | 8,021 (490) | 2,203 (538) | 94 (91.8) | 299 (73) |
| line XFER invalid: a>32 | 22,847 (87.2) | 1,417 (86.5) | 7,980 (487) | 2,355 (575) | 108 (105) | 365 (89.1) |
| line XFER invalid: ticker stopped | 600,726 (2.29e+03) | 35,789 (2.18e+03) | 23,269 (1.42e+03) | 65,740 (1.6e+04) | 2,289 (2.24e+03) | 9,318 (2.27e+03) |
| line XFER invalid: Manchester with sampling | 134,770 (514) | 10,358 (632) | 9,004 (550) | 16,438 (4.01e+03) | 576 (562) | 2,065 (504) |
| line XFER invalid: data pin not owned (drive) | 607,638 (2.32e+03) | 46,301 (2.83e+03) | 31,280 (1.91e+03) | 73,802 (1.8e+04) | 2,365 (2.31e+03) | 9,581 (2.34e+03) |
| line XFER invalid: pair pin not owned (drive with pair) | 644,942 (2.46e+03) | 49,798 (3.04e+03) | 33,646 (2.05e+03) | 77,936 (1.9e+04) | 2,745 (2.68e+03) | 10,018 (2.45e+03) |
| line XFER invalid: pair pin equals data pin (drive with pair) | 104,527 (399) | 7,883 (481) | 5,206 (318) | 12,444 (3.04e+03) | 398 (389) | 1,635 (399) |
| XFER invalid: c bit 7 | 22,176 (84.6) | 1,473 (89.9) | 7,717 (471) | 2,841 (694) | 105 (103) | 345 (84.2) |
| classic XFER with CRC feed (c bit 6) | 1,463,983 (5.58e+03) | 64,762 (3.95e+03) | 61,221 (3.74e+03) | 209,363 (5.11e+04) | 3,988 (3.89e+03) | 23,416 (5.72e+03) |
| stuff bit sent: runs of 1s, length 1 | 647,647 (2.47e+03) | 58,118 (3.55e+03) | 25,652 (1.57e+03) | 74,352 (1.82e+04) | 3,127 (3.05e+03) | 9,356 (2.28e+03) |
| stuff bit sent: runs of 1s, length 2 | 496,382 (1.89e+03) | 42,028 (2.57e+03) | 20,993 (1.28e+03) | 60,100 (1.47e+04) | 2,191 (2.14e+03) | 6,333 (1.55e+03) |
| stuff bit sent: runs of 1s, length 3 | 285,503 (1.09e+03) | 25,823 (1.58e+03) | 11,794 (720) | 35,358 (8.63e+03) | 1,256 (1.23e+03) | 4,521 (1.1e+03) |
| stuff bit sent: runs of 1s, length 4 | 188,536 (719) | 16,899 (1.03e+03) | 7,436 (454) | 20,507 (5.01e+03) | 635 (620) | 3,558 (869) |
| stuff bit sent: runs of 1s, length 5 | 134,297 (512) | 11,755 (717) | 4,822 (294) | 15,987 (3.9e+03) | 614 (600) | 2,309 (564) |
| stuff bit sent: runs of 1s, length 6 | 106,348 (406) | 9,471 (578) | 4,002 (244) | 11,665 (2.85e+03) | 529 (517) | 1,915 (468) |
| stuff bit sent: runs of 1s, length 7 | 47,268 (180) | 4,270 (261) | 1,709 (104) | 6,091 (1.49e+03) | 168 (164) | 680 (166) |
| stuff bit sent: runs of 1s, length 8 | 40,321 (154) | 3,844 (235) | 1,793 (109) | 4,280 (1.04e+03) | 150 (146) | 715 (175) |
| stuff bit sent: either polarity, length 2 | 1,601,576 (6.11e+03) | 136,305 (8.32e+03) | 73,142 (4.46e+03) | 186,867 (4.56e+04) | 4,935 (4.82e+03) | 26,461 (6.46e+03) |
| stuff bit sent: either polarity, length 3 | 873,618 (3.33e+03) | 71,141 (4.34e+03) | 35,741 (2.18e+03) | 108,752 (2.66e+04) | 2,514 (2.46e+03) | 11,457 (2.8e+03) |
| stuff bit sent: either polarity, length 4 | 589,061 (2.25e+03) | 46,050 (2.81e+03) | 26,718 (1.63e+03) | 72,271 (1.76e+04) | 2,629 (2.57e+03) | 8,879 (2.17e+03) |
| stuff bit sent: either polarity, length 5 | 424,436 (1.62e+03) | 34,078 (2.08e+03) | 17,316 (1.06e+03) | 51,591 (1.26e+04) | 1,367 (1.33e+03) | 7,597 (1.85e+03) |
| stuff bit sent: either polarity, length 6 | 328,817 (1.25e+03) | 25,606 (1.56e+03) | 14,190 (866) | 45,148 (1.1e+04) | 1,390 (1.36e+03) | 5,189 (1.27e+03) |
| stuff bit sent: either polarity, length 7 | 163,334 (623) | 12,407 (757) | 6,743 (412) | 19,517 (4.76e+03) | 949 (927) | 2,168 (529) |
| stuff bit sent: either polarity, length 8 | 138,754 (529) | 10,279 (627) | 5,823 (355) | 18,201 (4.44e+03) | 362 (354) | 1,956 (478) |
| stuff bit removed: runs of 1s, length 1 | 762,302 (2.91e+03) | 67,413 (4.11e+03) | 31,648 (1.93e+03) | 85,762 (2.09e+04) | 3,452 (3.37e+03) | 11,854 (2.89e+03) |
| stuff bit removed: runs of 1s, length 2 | 720,560 (2.75e+03) | 60,316 (3.68e+03) | 30,957 (1.89e+03) | 92,357 (2.25e+04) | 2,987 (2.92e+03) | 8,908 (2.17e+03) |
| stuff bit removed: runs of 1s, length 3 | 471,087 (1.8e+03) | 39,758 (2.43e+03) | 19,288 (1.18e+03) | 56,869 (1.39e+04) | 1,471 (1.44e+03) | 7,774 (1.9e+03) |
| stuff bit removed: runs of 1s, length 4 | 336,954 (1.29e+03) | 27,103 (1.65e+03) | 14,144 (863) | 38,994 (9.52e+03) | 1,630 (1.59e+03) | 5,144 (1.26e+03) |
| stuff bit removed: runs of 1s, length 5 | 257,392 (982) | 19,694 (1.2e+03) | 10,551 (644) | 29,367 (7.17e+03) | 1,491 (1.46e+03) | 4,253 (1.04e+03) |
| stuff bit removed: runs of 1s, length 6 | 208,816 (797) | 17,916 (1.09e+03) | 8,376 (511) | 23,850 (5.82e+03) | 972 (949) | 3,151 (769) |
| stuff bit removed: runs of 1s, length 7 | 93,191 (355) | 8,182 (499) | 3,772 (230) | 11,651 (2.84e+03) | 525 (513) | 1,342 (328) |
| stuff bit removed: runs of 1s, length 8 | 80,628 (308) | 6,997 (427) | 3,569 (218) | 9,644 (2.35e+03) | 302 (295) | 1,135 (277) |
| stuff bit removed: either polarity, length 2 | 1,797,048 (6.86e+03) | 155,347 (9.48e+03) | 81,936 (5e+03) | 213,108 (5.2e+04) | 4,834 (4.72e+03) | 28,195 (6.88e+03) |
| stuff bit removed: either polarity, length 3 | 924,483 (3.53e+03) | 74,380 (4.54e+03) | 38,831 (2.37e+03) | 112,374 (2.74e+04) | 2,824 (2.76e+03) | 13,287 (3.24e+03) |
| stuff bit removed: either polarity, length 4 | 615,264 (2.35e+03) | 49,422 (3.02e+03) | 26,989 (1.65e+03) | 75,109 (1.83e+04) | 2,240 (2.19e+03) | 10,405 (2.54e+03) |
| stuff bit removed: either polarity, length 5 | 451,184 (1.72e+03) | 36,913 (2.25e+03) | 18,341 (1.12e+03) | 57,270 (1.4e+04) | 1,345 (1.31e+03) | 7,759 (1.89e+03) |
| stuff bit removed: either polarity, length 6 | 348,806 (1.33e+03) | 29,735 (1.81e+03) | 13,710 (837) | 43,707 (1.07e+04) | 1,324 (1.29e+03) | 5,043 (1.23e+03) |
| stuff bit removed: either polarity, length 7 | 166,180 (634) | 14,240 (869) | 7,202 (440) | 20,063 (4.9e+03) | 856 (836) | 2,425 (592) |
| stuff bit removed: either polarity, length 8 | 140,657 (537) | 11,065 (675) | 5,860 (358) | 17,450 (4.26e+03) | 415 (405) | 1,991 (486) |
| stuff bit sent with NRZ | 1,801,178 (6.87e+03) | 152,859 (9.33e+03) | 75,793 (4.63e+03) | 216,644 (5.29e+04) | 5,870 (5.73e+03) | 27,227 (6.65e+03) |
| stuff bit sent with NRZI | 2,979,866 (1.14e+04) | 251,577 (1.54e+04) | 129,283 (7.89e+03) | 357,116 (8.72e+04) | 11,021 (1.08e+04) | 44,972 (1.1e+04) |
| stuff bit sent with Manchester | 1,284,854 (4.9e+03) | 103,638 (6.33e+03) | 52,798 (3.22e+03) | 156,927 (3.83e+04) | 5,925 (5.79e+03) | 20,895 (5.1e+03) |
| trailing stuff bit sent | 574,986 (2.19e+03) | 44,296 (2.7e+03) | 25,297 (1.54e+03) | 72,091 (1.76e+04) | 2,571 (2.51e+03) | 9,245 (2.26e+03) |
| trailing stuff bit removed | 633,612 (2.42e+03) | 51,046 (3.12e+03) | 27,615 (1.69e+03) | 76,712 (1.87e+04) | 2,530 (2.47e+03) | 9,741 (2.38e+03) |
| stuff error | 6,862,433 (2.62e+04) | 573,499 (3.5e+04) | 291,976 (1.78e+04) | 827,938 (2.02e+05) | 24,640 (2.41e+04) | 104,757 (2.56e+04) |
| stuff error on a trailing stuff bit | 589,464 (2.25e+03) | 47,456 (2.9e+03) | 25,549 (1.56e+03) | 71,527 (1.75e+04) | 2,241 (2.19e+03) | 9,052 (2.21e+03) |
| arbitration lost (NRZ) | 64,631 (247) | 5,463 (333) | 2,798 (171) | 6,700 (1.64e+03) | 232 (227) | 994 (243) |
| arbitration lost (NRZI) | 96,981 (370) | 8,471 (517) | 4,175 (255) | 10,964 (2.68e+03) | 362 (354) | 1,494 (365) |
| data 0 sent as 1 after arbitration loss | 2,629,637 (1e+04) | 245,404 (1.5e+04) | 104,776 (6.4e+03) | 302,982 (7.4e+04) | 9,684 (9.46e+03) | 42,096 (1.03e+04) |
| stuff cell sent as 1 after arbitration loss | 720,759 (2.75e+03) | 62,089 (3.79e+03) | 28,339 (1.73e+03) | 81,402 (1.99e+04) | 2,522 (2.46e+03) | 10,612 (2.59e+03) |
| drive+sample with the monitor on completed without loss | 336,139 (1.28e+03) | 27,227 (1.66e+03) | 14,550 (888) | 44,015 (1.07e+04) | 1,621 (1.58e+03) | 5,155 (1.26e+03) |
| SE0 end: 1 data bit left | 32,561 (124) | 3,494 (213) | 1,489 (90.9) | 5,120 (1.25e+03) | 58 (56.6) | 495 (121) |
| SE0 end: 2..31 data bits left | 437,249 (1.67e+03) | 26,953 (1.65e+03) | 19,182 (1.17e+03) | 55,533 (1.36e+04) | 1,808 (1.77e+03) | 6,818 (1.66e+03) |
| SE0 end: 32 data bits left | 18,537 (70.7) | 943 (57.6) | 1,174 (71.7) | 1,610 (393) | 42 (41) | 150 (36.6) |
| SE0 end in a drive+sample XFER | 166,946 (637) | 10,977 (670) | 7,170 (438) | 21,829 (5.33e+03) | 776 (758) | 2,041 (498) |
| SE0 end on a trailing stuff cell | 993 (3.79) | 32 (1.95) | 24 (1.46) | 126 (30.8) | 4 (3.91) | 11 (2.69) |
| SE0 on the pair without SE0 end (ignored) | 4,451,309 (1.7e+04) | 346,380 (2.11e+04) | 185,108 (1.13e+04) | 532,719 (1.3e+05) | 15,764 (1.54e+04) | 69,314 (1.69e+04) |
| Manchester second half | 32,535,480 (1.24e+05) | 2,403,998 (1.47e+05) | 1,303,353 (7.96e+04) | 3,938,385 (9.62e+05) | 134,044 (1.31e+05) | 522,700 (1.28e+05) |
| Manchester second half after the XFER completed | 2,251,132 (8.59e+03) | 151,313 (9.24e+03) | 92,853 (5.67e+03) | 290,063 (7.08e+04) | 10,195 (9.96e+03) | 36,560 (8.93e+03) |
| Manchester second half with pair | 12,408,741 (4.73e+04) | 904,971 (5.52e+04) | 488,818 (2.98e+04) | 1,478,665 (3.61e+05) | 47,565 (4.65e+04) | 196,818 (4.81e+04) |
| OUT on a Manchester second-half edge | 132,917 (507) | 6,616 (404) | 6,031 (368) | 14,606 (3.57e+03) | 1,048 (1.02e+03) | 3,396 (829) |
| SET on a Manchester second-half edge | 131,315 (501) | 6,124 (374) | 5,203 (318) | 16,766 (4.09e+03) | 396 (387) | 2,110 (515) |
| classic XFER issued on a Manchester second-half edge | 90,048 (344) | 5,404 (330) | 3,596 (219) | 11,165 (2.73e+03) | 322 (314) | 1,400 (342) |
| pair drive (complement on the clock-field pin) | 30,485,592 (1.16e+05) | 2,355,956 (1.44e+05) | 1,278,116 (7.8e+04) | 3,698,734 (9.03e+05) | 121,123 (1.18e+05) | 478,866 (1.17e+05) |
| NRZI drive from initial level 1 | 1,489,165 (5.68e+03) | 115,645 (7.06e+03) | 65,354 (3.99e+03) | 191,074 (4.66e+04) | 5,722 (5.59e+03) | 23,872 (5.83e+03) |
| line drive on an open-drain pin | 13,469,214 (5.14e+04) | 1,048,812 (6.4e+04) | 600,567 (3.67e+04) | 1,634,045 (3.99e+05) | 48,382 (4.72e+04) | 216,954 (5.3e+04) |
| sampling a pin another engine drives in a line XFER | 4,158,505 (1.59e+04) | 412,067 (2.52e+04) | 134,313 (8.2e+03) | 516,515 (1.26e+05) | 15,460 (1.51e+04) | 70,234 (1.71e+04) |
| sampling the engine's own data pin while driving it | 5,647,620 (2.15e+04) | 461,434 (2.82e+04) | 249,568 (1.52e+04) | 692,193 (1.69e+05) | 22,559 (2.2e+04) | 84,036 (2.05e+04) |
| line XFERs on 2 engines at once | 211,220,531 (8.06e+05) | 18,376,403 (1.12e+06) | 7,593,087 (4.63e+05) | 25,547,265 (6.24e+06) | 783,285 (7.65e+05) | 3,315,093 (8.09e+05) |
| line XFERs on 3 engines at once | 33,335,860 (1.27e+05) | 3,719,461 (2.27e+05) | 820,248 (5.01e+04) | 3,968,134 (9.69e+05) | 130,870 (1.28e+05) | 517,227 (1.26e+05) |
| line XFERs on 4 engines at once | 2,219,748 (8.47e+03) | 336,377 (2.05e+04) | 36,932 (2.25e+03) | 263,199 (6.43e+04) | 7,195 (7.03e+03) | 29,878 (7.29e+03) |
| mover moved a word during a line XFER | 441,700 (1.68e+03) | 36,825 (2.25e+03) | 15,323 (935) | 38,010 (9.28e+03) | 1,670 (1.63e+03) | 6,876 (1.68e+03) |
| host TX write to an engine in a line XFER | 923,042 (3.52e+03) | 89,052 (5.44e+03) | 38,873 (2.37e+03) | 84,401 (2.06e+04) | 3,523 (3.44e+03) | 14,437 (3.52e+03) |
| host RX read from an engine in a sampling line XFER | 738,682 (2.82e+03) | 66,772 (4.08e+03) | 25,611 (1.56e+03) | 115,313 (2.82e+04) | 2,822 (2.76e+03) | 11,830 (2.89e+03) |
| STOP of an engine in a line XFER | 291,990 (1.11e+03) | 24,968 (1.52e+03) | 12,940 (790) | 18,337 (4.48e+03) | 1,139 (1.11e+03) | 4,368 (1.07e+03) |
| BEGIN of an engine in a line XFER | 400,307 (1.53e+03) | 32,197 (1.97e+03) | 17,664 (1.08e+03) | 50,418 (1.23e+04) | 1,537 (1.5e+03) | 6,354 (1.55e+03) |
| reset or deselect during a line XFER | 14,523 (55.4) | 1,014 (61.9) | 707 (43.2) | 225 (54.9) | 54 (52.7) | 215 (52.5) |
| START clears non-zero line-unit state | 4,416,037 (1.68e+04) | 287,284 (1.75e+04) | 318,816 (1.95e+04) | 612,899 (1.5e+05) | 17,261 (1.69e+04) | 68,541 (1.67e+04) |
| fault with the ticker running (unit state kept) | 2,595,781 (9.9e+03) | 171,285 (1.05e+04) | 259,178 (1.58e+04) | 321,667 (7.85e+04) | 10,501 (1.03e+04) | 40,302 (9.84e+03) |
| LTIM while a Manchester second half is pending | 234,032 (893) | 12,466 (761) | 9,823 (600) | 28,856 (7.04e+03) | 646 (631) | 5,789 (1.41e+03) |
| LCFG while a Manchester second half is pending | 294,701 (1.12e+03) | 16,341 (997) | 11,666 (712) | 38,597 (9.42e+03) | 1,160 (1.13e+03) | 5,673 (1.39e+03) |
| classic XFER while a Manchester second half is pending | 262,327 (1e+03) | 16,019 (978) | 11,055 (675) | 30,266 (7.39e+03) | 918 (896) | 3,642 (889) |
| START while a Manchester second half is pending | 12,078 (46.1) | 1,105 (67.4) | 357 (21.8) | 1,734 (423) | 32 (31.2) | 200 (48.8) |
| tick with fraction carry at P=1 (2 cycles) | 82,920,491 (3.16e+05) | 5,039,139 (3.08e+05) | 3,407,872 (2.08e+05) | 11,160,534 (2.72e+06) | 324,672 (3.17e+05) | 1,392,867 (3.4e+05) |
| line XFER completed with a fraction (Q!=0): drive | 2,809,600 (1.07e+04) | 200,328 (1.22e+04) | 117,522 (7.17e+03) | 360,695 (8.81e+04) | 10,577 (1.03e+04) | 47,728 (1.17e+04) |
| line XFER completed with a fraction (Q!=0): sample | 1,492,487 (5.69e+03) | 114,912 (7.01e+03) | 65,554 (4e+03) | 187,018 (4.57e+04) | 5,444 (5.32e+03) | 23,786 (5.81e+03) |
| line XFER completed with a fraction (Q!=0): drive+sample | 896,986 (3.42e+03) | 72,185 (4.41e+03) | 39,594 (2.42e+03) | 111,407 (2.72e+04) | 3,399 (3.32e+03) | 14,627 (3.57e+03) |
| line XFER completed after a trailing stuff bit: drive | 354,714 (1.35e+03) | 26,085 (1.59e+03) | 15,597 (952) | 45,930 (1.12e+04) | 1,594 (1.56e+03) | 5,876 (1.43e+03) |
| line XFER completed after a trailing stuff bit: sample | 414,440 (1.58e+03) | 32,918 (2.01e+03) | 17,952 (1.1e+03) | 50,677 (1.24e+04) | 1,555 (1.52e+03) | 6,380 (1.56e+03) |
| line XFER completed after a trailing stuff bit: drive+sample | 219,172 (836) | 18,128 (1.11e+03) | 9,663 (590) | 26,035 (6.36e+03) | 975 (952) | 3,361 (821) |
| protocol-shaped: NRZI, stuffing runs of 1s length 6, pair, SE0 end, sampling XFER ended | 13,248 (50.5) | 776 (47.4) | 386 (23.6) | 1,378 (336) | 78 (76.2) | 128 (31.2) |
| protocol-shaped: NRZ, stuffing either polarity length 5, monitor, drive+sample XFER completed | 11,200 (42.7) | 927 (56.6) | 361 (22) | 1,665 (406) | 22 (21.5) | 224 (54.7) |
| protocol-shaped: Manchester drive with pair completed | 1,083,893 (4.13e+03) | 73,367 (4.48e+03) | 44,977 (2.75e+03) | 131,844 (3.22e+04) | 4,542 (4.44e+03) | 18,282 (4.46e+03) |
