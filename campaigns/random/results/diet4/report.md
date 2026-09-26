| campaign | generator | level | seeds | cases/seed | host cycles/case | cases run | passed | failed | infra errors | programs loaded + reloads | lockstep cycles | instructions completed | sim CPU-h | Slurm array |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `d4-rtl-default` | default | RTL | 0x1..0x800 (2048) | 64 | 2,000 | 131,072 | 131,072 | 0 | 0 | 461,482 + 101,888 | 495,478,116 | 223,904,612 | 26.9 | 23974967 |
| `d4-rtl-xcov-default` | default | RTL | 0x801..0xc00 (1024) | 64 | 2,000 | 65,536 | 65,536 | 0 | 0 | 230,862 + 51,133 | 247,744,648 | 112,223,677 | 16.6 | 23974968 |
| `d4-rtl-long` | default | RTL | 0x100001..0x100100 (256) | 64 | 20,000 | 16,384 | 16,384 | 0 | 0 | 57,731 + 127,019 | 356,937,617 | 291,795,632 | 23.0 | 23974969 |
| `d4-rtl-xcov-dense` | dense | RTL | 0x300001..0x300100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,652 + 53,421 | 176,974,205 | 34,319,621 | 10.9 | 23974972 |
| `d4-rtl-xcov-faulty` | faulty | RTL | 0x400001..0x400100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,640 + 45,940 | 160,250,428 | 93,821,727 | 11.2 | 23974973 |
| `d4-rtl-xcov-hostile` | hostile | RTL | 0x500001..0x500100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,579 + 35,940 | 158,958,462 | 76,256,239 | 12.7 | 23974974 |
| `d4-rtl-xcov-deselect` | deselect | RTL | 0x600001..0x600100 (256) | 64 | 2,000 | 16,384 | 16,384 | 0 | 0 | 57,481 + 13,171 | 61,540,549 | 17,095,722 | 5.3 | 23974976 |
| `d4-gl-default` | default | GL | 0x1..0x40 (64) | 8 | 2,000 | 512 | 512 | 0 | 0 | 1,830 + 371 | 1,941,597 | 907,287 | 0.7 | 23975172 |
| `d4-gl-extended` | default | GL | 0x41..0x140 (256) | 32 | 2,000 | 8,192 | 8,192 | 0 | 0 | 28,882 + 6,328 | 30,945,971 | 13,976,812 | 9.9 | 23975174 |
| `d4-gl-hostile` | hostile | GL | 0x500001..0x500040 (64) | 16 | 8,000 | 1,024 | 1,024 | 0 | 0 | 3,613 + 2,141 | 9,940,328 | 4,906,410 | 3.3 | 23975175 |
| `d4-gl-deselect` | deselect | GL | 0x600001..0x600040 (64) | 16 | 2,000 | 1,024 | 1,024 | 0 | 0 | 3,572 + 811 | 3,845,946 | 1,038,160 | 1.2 | 23975176 |
| **total** | | | 4,800 seeds | | | **289,280** | **289,280** | **0** | 0 | 1,018,324 + 438,163 | **1,704,557,867** | 870,245,899 | 121.9 | |

Merged coverage, all campaigns (bins from `random_gen.Coverage`, counted on the model in lockstep):

| opcode | engine 0 | engine 1 | engine 2 | engine 3 |
|---|---:|---:|---:|---:|
| NOP | 2,652,209 | 2,446,211 | 2,299,899 | 2,218,958 |
| HALT | 117,869 | 118,876 | 115,927 | 116,805 |
| SET | 21,802,942 | 20,770,304 | 19,694,517 | 18,315,160 |
| DIR | 10,487,388 | 10,141,912 | 9,566,399 | 9,084,245 |
| WAIT | 4,543,311 | 4,348,292 | 4,091,269 | 3,883,774 |
| JMP | 46,094,278 | 44,029,531 | 41,286,165 | 39,012,899 |
| PULL | 258,516 | 257,516 | 255,784 | 254,217 |
| PUSH | 1,263,110 | 1,255,187 | 1,250,676 | 1,241,395 |
| OUT | 10,790,700 | 10,287,938 | 9,560,506 | 8,917,476 |
| IN | 8,812,886 | 8,548,130 | 8,134,721 | 7,676,445 |
| COUNT | 3,007,313 | 2,828,871 | 2,801,815 | 2,578,353 |
| LOOP | 3,404,976 | 3,096,204 | 3,039,139 | 2,878,505 |
| LIMIT | 13,317,699 | 12,814,697 | 12,277,230 | 11,446,319 |
| WAITPIN | 2,242,167 | 2,160,956 | 1,936,460 | 1,867,893 |
| SIGNAL | 3,937,290 | 3,668,990 | 3,570,587 | 3,325,002 |
| WAITEVENT | 464,881 | 460,931 | 454,758 | 442,709 |
| PINS | 24,969,220 | 23,838,814 | 22,405,185 | 20,770,851 |
| XFER | 2,068,231 | 1,913,410 | 1,847,626 | 1,757,093 |
| MOV | 10,351,252 | 9,700,133 | 9,322,622 | 8,550,323 |
| LOAD | 4,378,841 | 4,154,007 | 3,913,541 | 3,621,810 |
| ADD | 3,284,676 | 3,157,217 | 2,910,996 | 2,874,683 |
| XOR | 5,573,164 | 5,419,478 | 4,857,202 | 4,881,194 |
| AND | 3,491,062 | 3,149,756 | 3,119,438 | 2,841,680 |
| OR | 3,194,982 | 3,171,373 | 2,911,483 | 2,760,649 |
| SHL | 1,865,745 | 1,914,788 | 1,706,183 | 1,607,064 |
| SHR | 1,995,747 | 1,854,676 | 1,751,016 | 1,644,811 |
| JZ | 35,045,925 | 33,732,487 | 31,017,919 | 29,405,839 |
| NOT | 2,102,059 | 2,025,835 | 1,895,400 | 1,744,092 |
| TIME | 3,670,839 | 3,557,492 | 3,357,294 | 3,160,608 |

Stall cycles: PULL 195,573,867, PUSH 578,381,288, WAITPIN 94,408,214, WAITEVENT 162,353,652

Faults (code from instruction): code 1 from DIR 72,770, code 1 from INVALID 37,632, code 1 from LIMIT 38,908, code 1 from MOV 38,287, code 1 from NOP 38,512, code 1 from OUT 77,382, code 1 from PINS 37,302, code 1 from PUSH 37,533, code 1 from SET 48,672, code 1 from SHL 162,847, code 1 from SHR 124,742, code 1 from SIGNAL 38,283, code 1 from XFER 37,951, code 2 from PC-RANGE 418,067, code 3 from WAITEVENT 83,815, code 3 from WAITPIN 65,821, code 4 from PUSH 262,657; explicit `FAULT n`: 255 distinct codes, 164,596 faults

XFER: 32/32 CPOL/CPHA/bit-order/drive/sample combinations issued (min 110,604, max 373,647 per combination); bits=1 2,299,118, bits 2..width-1 4,898,553, bits=width 501,861

Mover (DMA) word transfers: 373,011

Host commands: BEGIN accepted 1,614,909, BEGIN rejected 102,617, CLEAR accepted 1,642,048, CLEAR rejected 273,365, COMMIT accepted 1,546,834, COMMIT rejected 211,440, EVENT accepted 1,170,462, EVENT rejected 84,371, FLUSH accepted 67,923, FLUSH rejected 48,650, OWN accepted 1,490,635, OWN rejected 82,558, READ_SELECT accepted 21,638,672, READ_SELECT rejected 78,157, ROUTE accepted 757,565, ROUTE rejected 84,092, SELECT accepted 32,267,413, SELECT rejected 77,985, START accepted 3,428,435, START rejected 639,630, STOP accepted 640,907, STOP rejected 83,767, TRIGGER accepted 426,892, TRIGGER rejected 96,819, invalid-op rejected 117,432

Host traffic: deselect (ena low) 44,691, engine reprogrammed 438,163, partial read abandoned (window 0) 439,072, partial read abandoned (window 3) 438,529, partial write abandoned (window 0) 291,585, partial write abandoned (window 1) 3,688,402, partial write abandoned (window 2) 291,545, program word not accepted 58,634, program word written 938,101, revive: cleared fault 1,310,198, revive: restarted 1,929,157, rx read abandoned (empty) 1,753,030, rx word read 3,563,720, status read select=0 366,150, status read select=1 365,466, status read select=2 364,849, status read select=3 366,011, status read select=4 365,509, status read select=5 365,933, status read select=6 363,694, status read select=7 365,046, tx word accepted 2,764,301, tx write abandoned (full) 4,830,990

Other: byte-lane shift count faults (code 1) 249,635, cycles with IRQ asserted 1,568,672,089, cycles with any pin driven 763,954,711, cycles with fault output asserted 1,350,763,097, engine starts 3,320,774, jump targets saturated to PC 127 171,894

Holes (bins never hit), all campaigns: `{"opcode_x_engine": {"0": [], "1": [], "2": [], "3": []}, "engines_without_any_completion": [], "stall_kinds": [], "fault_bins": [], "xfer_flag_combinations": [], "xfer_bit_counts": [], "host_commands": [], "host_traffic": [], "misc": [], "xcov": []}`

Holes, upstream generator only (`d4-rtl-default`): `{"opcode_x_engine": {"0": [], "1": [], "2": [], "3": []}, "engines_without_any_completion": [], "stall_kinds": [], "fault_bins": [], "xfer_flag_combinations": [], "xfer_bit_counts": [], "host_commands": [], "host_traffic": [], "misc": []}`

Failures: none

Extended cross coverage (`xcov.py`): hits per campaign, and hits per 1,000 cases in parentheses; sorted by the rarest rate over all xcov campaigns.

| bin | `d4-rtl-xcov-default` (65,536 cases) | `d4-rtl-xcov-dense` (16,384 cases) | `d4-rtl-xcov-faulty` (16,384 cases) | `d4-rtl-xcov-hostile` (16,384 cases) | `d4-rtl-xcov-deselect` (16,384 cases) |
|---|---:|---:|---:|---:|---:|
| synchronous start of 4 engines | 13 (0.198) | 29 (1.77) | 2 (0.122) | 8 (0.488) | 3 (0.183) |
| mover blocked: host TX write to dest, same edge | 75 (1.14) | 31 (1.89) | 11 (0.671) | 24 (1.46) | 13 (0.793) |
| host TX write + engine PULL on same FIFO, same edge | 59 (0.9) | 77 (4.7) | 18 (1.1) | 39 (2.38) | 11 (0.671) |
| synchronous start of 3 engines | 271 (4.14) | 258 (15.7) | 98 (5.98) | 120 (7.32) | 24 (1.46) |
| mover arbitration: >=2 routes eligible | 180 (2.75) | 652 (39.8) | 73 (4.46) | 223 (13.6) | 13 (0.793) |
| mover push into FIFO the host writes next edge (dest == selected, window 2) | 766 (11.7) | 586 (35.8) | 181 (11) | 258 (15.7) | 148 (9.03) |
| WAITPIN/WAITEVENT timeout with LIMIT 1 | 565 (8.62) | 703 (42.9) | 396 (24.2) | 321 (19.6) | 89 (5.43) |
| engines in XFER: 4 | 270 (4.12) | 498 (30.4) | 794 (48.5) | 756 (46.1) | 20 (1.22) |
| host RX pop + engine PUSH on same FIFO, same edge | 862 (13.2) | 1,118 (68.2) | 433 (26.4) | 492 (30) | 119 (7.26) |
| mover push + engine PULL on same dest, same edge | 1,092 (16.7) | 921 (56.2) | 369 (22.5) | 486 (29.7) | 190 (11.6) |
| EVENT command and SIGNAL on same edge | 1,562 (23.8) | 584 (35.6) | 1,225 (74.8) | 965 (58.9) | 218 (13.3) |
| synchronous start of 2 engines | 1,641 (25) | 1,474 (90) | 689 (42.1) | 835 (51) | 211 (12.9) |
| mover pop + engine PUSH on same source, same edge | 4,142 (63.2) | 2,591 (158) | 1,091 (66.6) | 1,500 (91.6) | 831 (50.7) |
| STOP/BEGIN of engine inside WAIT | 4,409 (67.3) | 1,858 (113) | 1,737 (106) | 2,089 (128) | 347 (21.2) |
| FLUSH cleared an active route | 2,754 (42) | 3,977 (243) | 3,300 (201) | 2,360 (144) | 464 (28.3) |
| strict PUSH overflow (fault 4) while host holds that RX head | 3,491 (53.3) | 6,603 (403) | 2,628 (160) | 2,380 (145) | 533 (32.5) |
| mover route exhausted (count reached 0) | 7,066 (108) | 5,147 (314) | 2,294 (140) | 2,911 (178) | 1,515 (92.5) |
| reset/deselect inside XFER | 10,460 (160) | 2,732 (167) | 2,327 (142) | 2,436 (149) | 3,072 (188) |
| reset/deselect with engines running | 5,754 (87.8) | 1,363 (83.2) | 1,506 (91.9) | 1,299 (79.3) | 14,561 (889) |
| mover blocked: source RX head reserved by host read | 10,497 (160) | 9,184 (561) | 2,168 (132) | 5,529 (337) | 1,273 (77.7) |
| STOP/BEGIN of engine inside XFER | 14,441 (220) | 7,570 (462) | 5,940 (363) | 7,177 (438) | 1,235 (75.4) |
| mover route to self moved a word | 17,943 (274) | 13,213 (806) | 5,317 (325) | 7,638 (466) | 3,960 (242) |
| trigger delivered to engine blocked in WAITEVENT | 16,630 (254) | 7,071 (432) | 14,057 (858) | 13,972 (853) | 2,579 (157) |
| host RX read abandoned mid-word (window change) | 14,367 (219) | 20,988 (1.28e+03) | 11,572 (706) | 10,353 (632) | 1,945 (119) |
| ROUTE edited an active route | 16,975 (259) | 17,364 (1.06e+03) | 15,177 (926) | 11,289 (689) | 2,843 (174) |
| host fault flag cleared | 37,054 (565) | 17,099 (1.04e+03) | 14,682 (896) | 17,205 (1.05e+03) | 9,647 (589) |
| HALT with output enables active | 31,707 (484) | 16,379 (1e+03) | 34,417 (2.1e+03) | 16,750 (1.02e+03) | 5,639 (344) |
| WAITEVENT satisfied after blocking | 41,667 (636) | 25,295 (1.54e+03) | 25,887 (1.58e+03) | 25,068 (1.53e+03) | 5,958 (364) |
| engines in XFER: 3 | 56,083 (856) | 45,179 (2.76e+03) | 20,196 (1.23e+03) | 30,580 (1.87e+03) | 6,466 (395) |
| mover moved a word | 70,628 (1.08e+03) | 45,244 (2.76e+03) | 20,188 (1.23e+03) | 26,643 (1.63e+03) | 15,265 (932) |
| STOP/BEGIN of engine stalled on PULL/PUSH/WAITPIN/WAITEVENT | 81,213 (1.24e+03) | 47,615 (2.91e+03) | 32,576 (1.99e+03) | 42,504 (2.59e+03) | 5,802 (354) |
| WAIT >= 20 cycles issued | 66,085 (1.01e+03) | 46,253 (2.82e+03) | 45,151 (2.76e+03) | 42,025 (2.57e+03) | 10,827 (661) |
| host fault flag set | 90,641 (1.38e+03) | 32,670 (1.99e+03) | 30,378 (1.85e+03) | 34,556 (2.11e+03) | 27,554 (1.68e+03) |
| WAITEVENT consumed event with simultaneous new delivery | 82,709 (1.26e+03) | 34,311 (2.09e+03) | 61,400 (3.75e+03) | 54,669 (3.34e+03) | 8,540 (521) |
| XFER issued with half-period >= 4 | 117,761 (1.8e+03) | 79,057 (4.83e+03) | 85,311 (5.21e+03) | 70,517 (4.3e+03) | 18,666 (1.14e+03) |
| fault with output enables active | 93,143 (1.42e+03) | 114,221 (6.97e+03) | 200,724 (1.23e+04) | 56,750 (3.46e+03) | 16,729 (1.02e+03) |
| WAITEVENT satisfied on issue | 179,302 (2.74e+03) | 113,510 (6.93e+03) | 137,080 (8.37e+03) | 124,315 (7.59e+03) | 23,763 (1.45e+03) |
| WAITPIN satisfied after blocking | 293,472 (4.48e+03) | 125,704 (7.67e+03) | 211,406 (1.29e+04) | 203,097 (1.24e+04) | 44,157 (2.7e+03) |
| XFER issued with half-period 1 | 519,998 (7.93e+03) | 233,755 (1.43e+04) | 427,410 (2.61e+04) | 349,787 (2.13e+04) | 82,619 (5.04e+03) |
| LOOP taken | 715,441 (1.09e+04) | 474,222 (2.89e+04) | 517,270 (3.16e+04) | 445,421 (2.72e+04) | 106,458 (6.5e+03) |
| WAITPIN satisfied on issue | 776,753 (1.19e+04) | 265,435 (1.62e+04) | 644,188 (3.93e+04) | 551,378 (3.37e+04) | 118,826 (7.25e+03) |
| JZ not taken | 950,638 (1.45e+04) | 531,056 (3.24e+04) | 617,234 (3.77e+04) | 596,809 (3.64e+04) | 134,380 (8.2e+03) |
| LOOP fell through | 863,588 (1.32e+04) | 648,135 (3.96e+04) | 816,970 (4.99e+04) | 602,606 (3.68e+04) | 131,009 (8e+03) |
| mover arbitration: 4 routes configured | 181,368 (2.77e+03) | 1,185,083 (7.23e+04) | 1,179,897 (7.2e+04) | 712,910 (4.35e+04) | 7,774 (474) |
| engines in XFER: 2 | 1,827,090 (2.79e+04) | 1,359,890 (8.3e+04) | 970,635 (5.92e+04) | 1,194,388 (7.29e+04) | 203,901 (1.24e+04) |
| trigger mode 0 (rise) detected | 4,049,868 (6.18e+04) | 3,250,998 (1.98e+05) | 2,632,292 (1.61e+05) | 2,692,821 (1.64e+05) | 578,302 (3.53e+04) |
| trigger mode 1 (fall) detected | 3,928,514 (5.99e+04) | 3,261,189 (1.99e+05) | 2,784,322 (1.7e+05) | 2,773,715 (1.69e+05) | 568,220 (3.47e+04) |
| engines running: 4 | 6,708,752 (1.02e+05) | 2,697,934 (1.65e+05) | 3,668,160 (2.24e+05) | 2,610,474 (1.59e+05) | 852,679 (5.2e+04) |
| IRQ from RX data only | 15,797,752 (2.41e+05) | 5,586,819 (3.41e+05) | 4,364,241 (2.66e+05) | 4,997,866 (3.05e+05) | 3,654,552 (2.23e+05) |
| JZ taken | 15,672,149 (2.39e+05) | 7,549,504 (4.61e+05) | 12,847,827 (7.84e+05) | 10,647,161 (6.5e+05) | 2,347,136 (1.43e+05) |
| open-drain pin released (logical 1) | 18,286,816 (2.79e+05) | 9,200,947 (5.62e+05) | 10,295,006 (6.28e+05) | 9,692,255 (5.92e+05) | 2,711,970 (1.66e+05) |
| open-drain pin pulled low | 23,879,233 (3.64e+05) | 13,672,958 (8.35e+05) | 13,153,694 (8.03e+05) | 13,021,576 (7.95e+05) | 3,556,272 (2.17e+05) |
| engines running: 3 | 28,368,288 (4.33e+05) | 15,180,639 (9.27e+05) | 19,704,408 (1.2e+06) | 15,582,662 (9.51e+05) | 3,815,053 (2.33e+05) |
| trigger mode 2 (high) detected | 27,215,573 (4.15e+05) | 21,561,681 (1.32e+06) | 19,906,746 (1.22e+06) | 18,640,825 (1.14e+06) | 3,877,737 (2.37e+05) |
| trigger mode 3 (low) detected | 28,404,155 (4.33e+05) | 22,452,999 (1.37e+06) | 20,180,264 (1.23e+06) | 19,799,336 (1.21e+06) | 3,977,220 (2.43e+05) |
| RX FIFO full e3 | 27,933,458 (4.26e+05) | 30,278,024 (1.85e+06) | 20,409,014 (1.25e+06) | 22,582,374 (1.38e+06) | 2,756,554 (1.68e+05) |
| RX FIFO full e2 | 29,312,426 (4.47e+05) | 31,821,102 (1.94e+06) | 20,250,994 (1.24e+06) | 22,281,778 (1.36e+06) | 3,369,977 (2.06e+05) |
| RX FIFO full e1 | 29,946,203 (4.57e+05) | 32,793,247 (2e+06) | 20,229,314 (1.23e+06) | 22,759,745 (1.39e+06) | 4,138,567 (2.53e+05) |
| RX FIFO full e0 | 31,269,864 (4.77e+05) | 33,178,948 (2.03e+06) | 20,326,299 (1.24e+06) | 23,645,613 (1.44e+06) | 4,747,350 (2.9e+05) |
| mover blocked: dest TX FIFO full | 36,267,954 (5.53e+05) | 58,378,323 (3.56e+06) | 37,077,406 (2.26e+06) | 39,241,513 (2.4e+06) | 3,673,747 (2.24e+05) |
| engines running: 2 | 53,132,548 (8.11e+05) | 38,595,891 (2.36e+06) | 43,270,277 (2.64e+06) | 39,527,703 (2.41e+06) | 7,776,702 (4.75e+05) |
| push-pull pin driven high | 56,209,908 (8.58e+05) | 38,005,665 (2.32e+06) | 47,625,544 (2.91e+06) | 39,440,901 (2.41e+06) | 8,824,384 (5.39e+05) |
| engines running: 1 | 59,492,950 (9.08e+05) | 57,695,026 (3.52e+06) | 49,716,367 (3.03e+06) | 51,871,110 (3.17e+06) | 12,310,998 (7.51e+05) |
| IRQ from event only | 78,228,450 (1.19e+06) | 36,025,635 (2.2e+06) | 53,204,518 (3.25e+06) | 45,773,244 (2.79e+06) | 24,237,220 (1.48e+06) |
| push-pull pin driven low | 72,391,105 (1.1e+06) | 54,681,602 (3.34e+06) | 58,026,490 (3.54e+06) | 49,817,147 (3.04e+06) | 11,020,268 (6.73e+05) |
| engines running: 0 | 99,748,062 (1.52e+06) | 62,730,768 (3.83e+06) | 43,817,317 (2.67e+06) | 49,292,882 (3.01e+06) | 36,652,518 (2.24e+06) |
| TX FIFO full e3 | 95,019,831 (1.45e+06) | 105,263,115 (6.42e+06) | 107,038,993 (6.53e+06) | 98,633,948 (6.02e+06) | 9,708,476 (5.93e+05) |
| TX FIFO full e2 | 97,556,362 (1.49e+06) | 106,779,187 (6.52e+06) | 108,235,926 (6.61e+06) | 99,251,121 (6.06e+06) | 10,242,272 (6.25e+05) |
| TX FIFO full e1 | 100,324,849 (1.53e+06) | 107,780,254 (6.58e+06) | 108,058,542 (6.6e+06) | 100,012,615 (6.1e+06) | 10,839,225 (6.62e+05) |
| TX FIFO full e0 | 103,534,429 (1.58e+06) | 108,786,227 (6.64e+06) | 108,966,101 (6.65e+06) | 99,953,766 (6.1e+06) | 11,785,067 (7.19e+05) |
| IRQ from event and RX data | 126,481,707 (1.93e+06) | 126,707,795 (7.73e+06) | 94,828,199 (5.79e+06) | 100,500,095 (6.13e+06) | 16,002,795 (9.77e+05) |

xcov observer errors: none
