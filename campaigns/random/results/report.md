| campaign | generator | level | seeds | cases/seed | host cycles/case | cases run | passed | failed | infra errors | programs loaded + reloads | lockstep cycles | instructions completed | sim CPU-h | Slurm array |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `rtl-default` | default | RTL | 0x1..0x1000 (4096) | 64 | 2,000 | 262,144 | 262,144 | 0 | 0 | 922,535 + 210,106 | 1,018,395,195 | 518,291,035 | 71.4 | 23746017 |
| `rtl-xcov-default` | default | RTL | 0x1001..0x1800 (2048) | 64 | 2,000 | 131,072 | 131,072 | 0 | 0 | 461,218 + 105,613 | 508,953,932 | 259,520,748 | 41.2 | 23750843 |
| `rtl-long` | default | RTL | 0x100001..0x100100 (256) | 64 | 20,000 | 16,384 | 16,384 | 0 | 0 | 57,714 + 126,091 | 358,623,049 | 321,741,605 | 23.0 | 23746365 |
| `rtl-xlong` | default | RTL | 0x200001..0x200040 (64) | 16 | 200,000 | 1,024 | 1,024 | 0 | 0 | 3,591 + 78,286 | 206,726,151 | 229,357,705 | 16.2 | 23746366 |
| `rtl-dense` | dense | RTL | 0x300001..0x300100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,737 + 53,102 | 178,881,555 | 44,503,461 | 9.6 | 23746367 |
| `rtl-xcov-dense` | dense | RTL | 0x300101..0x300200 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,607 + 53,593 | 178,847,322 | 44,475,241 | 12.3 | 23750844 |
| `rtl-faulty` | faulty | RTL | 0x400001..0x400100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,630 + 45,756 | 161,876,329 | 102,720,654 | 14.2 | 23746368 |
| `rtl-xcov-faulty` | faulty | RTL | 0x400101..0x400200 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,745 + 45,438 | 161,880,381 | 103,304,672 | 15.1 | 23750845 |
| `rtl-hostile` | hostile | RTL | 0x500001..0x500100 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,593 + 35,848 | 160,799,172 | 86,821,930 | 12.3 | 23747218 |
| `rtl-xcov-hostile` | hostile | RTL | 0x500101..0x500200 (256) | 64 | 8,000 | 16,384 | 16,384 | 0 | 0 | 57,811 + 35,907 | 160,820,358 | 86,343,718 | 10.5 | 23750846 |
| `rtl-xcov-deselect` | deselect | RTL | 0x600001..0x600100 (256) | 64 | 2,000 | 16,384 | 16,384 | 0 | 0 | 57,707 + 13,296 | 63,544,269 | 31,192,981 | 5.2 | 23752081 |
| `gl-default` | default | GL | 0x1..0x40 (64) | 8 | 2,000 | 512 | 512 | 0 | 0 | 1,833 + 402 | 1,990,207 | 982,956 | 1.9 | 23746061 |
| `gl-extended` | default | GL | 0x41..0x140 (256) | 32 | 2,000 | 8,192 | 8,192 | 0 | 0 | 28,891 + 6,500 | 31,823,649 | 16,240,618 | 24.7 | 23746369 |
| `gl-hostile` | hostile | GL | 0x500001..0x500040 (64) | 16 | 8,000 | 1,024 | 1,024 | 0 | 0 | 3,605 + 2,156 | 10,056,054 | 5,582,457 | 8.7 | 23752532 |
| `gl-deselect` | deselect | GL | 0x600001..0x600040 (64) | 16 | 2,000 | 1,024 | 1,024 | 0 | 0 | 3,619 + 792 | 3,967,201 | 1,836,679 | 2.6 | 23752533 |
| **total** | | | 8,704 seeds | | | **536,064** | **536,064** | **0** | 0 | 1,886,836 + 812,886 | **3,207,184,824** | 1,852,916,460 | 269.0 | |

Merged coverage, all campaigns (bins from `random_gen.Coverage`, counted on the model in lockstep):

| opcode | engine 0 | engine 1 | engine 2 | engine 3 |
|---|---:|---:|---:|---:|
| NOP | 5,546,817 | 5,240,133 | 4,972,022 | 4,692,244 |
| HALT | 249,844 | 251,170 | 249,732 | 249,216 |
| SET | 45,862,362 | 43,403,981 | 41,098,364 | 38,638,607 |
| DIR | 22,041,343 | 21,116,316 | 20,051,057 | 18,909,631 |
| WAIT | 10,024,385 | 9,265,574 | 8,999,797 | 8,382,970 |
| JMP | 97,148,157 | 92,138,124 | 86,708,108 | 81,326,370 |
| PULL | 669,434 | 662,535 | 660,706 | 655,884 |
| PUSH | 3,951,258 | 3,933,805 | 3,909,610 | 3,853,748 |
| OUT | 23,331,876 | 22,155,494 | 20,999,950 | 19,784,145 |
| IN | 19,373,966 | 18,244,454 | 17,236,858 | 16,293,661 |
| COUNT | 6,277,255 | 6,003,852 | 5,767,197 | 5,401,909 |
| LOOP | 6,954,445 | 6,851,910 | 6,467,549 | 6,040,019 |
| LIMIT | 27,815,808 | 26,666,680 | 24,865,077 | 23,619,753 |
| WAITPIN | 4,776,000 | 4,639,136 | 4,262,370 | 3,933,004 |
| SIGNAL | 8,421,523 | 7,821,075 | 7,294,083 | 7,154,151 |
| WAITEVENT | 1,144,672 | 1,097,935 | 1,034,018 | 939,708 |
| PINS | 51,406,302 | 49,342,465 | 45,965,577 | 43,601,918 |
| XFER | 4,472,524 | 4,295,553 | 4,093,574 | 3,860,490 |
| MOV | 22,363,382 | 20,777,378 | 20,436,556 | 18,912,591 |
| LOAD | 9,105,500 | 8,560,939 | 8,128,201 | 7,822,518 |
| ADD | 6,829,525 | 6,440,191 | 6,193,473 | 5,893,471 |
| XOR | 11,726,211 | 11,243,954 | 10,747,646 | 10,132,981 |
| AND | 7,325,947 | 6,743,054 | 6,450,224 | 6,116,397 |
| OR | 6,771,310 | 6,719,508 | 6,212,431 | 5,947,971 |
| SHL | 4,761,148 | 4,633,264 | 4,339,114 | 4,073,348 |
| SHR | 4,836,907 | 4,384,799 | 4,337,048 | 4,170,069 |
| JZ | 75,566,471 | 72,012,884 | 67,635,468 | 63,720,975 |
| NOT | 4,560,632 | 4,306,364 | 4,015,063 | 3,769,895 |
| TIME | 7,986,654 | 7,579,512 | 7,238,231 | 6,816,015 |

Stall cycles: PULL 400,900,456, PUSH 1,021,998,618, WAITPIN 201,824,927, WAITEVENT 345,532,771

Faults (code from instruction): code 1 from DIR 164,389, code 1 from INVALID 112,272, code 1 from LIMIT 114,196, code 1 from MOV 112,545, code 1 from NOP 112,717, code 1 from OUT 228,046, code 1 from PINS 110,726, code 1 from PUSH 111,214, code 1 from SET 127,634, code 1 from SHL 111,967, code 1 from SIGNAL 111,042, code 1 from XFER 113,228, code 2 from PC-RANGE 547,709, code 3 from WAITEVENT 186,849, code 3 from WAITPIN 149,402, code 4 from PUSH 450,956; explicit `FAULT n`: 255 distinct codes, 416,804 faults

XFER: 32/32 CPOL/CPHA/bit-order/drive/sample combinations issued (min 257,153, max 821,634 per combination); bits=1 4,910,533, bits 2..width-1 10,886,643, bits=width 1,175,597

Mover (DMA) word transfers: 1,115,787

Host commands: BEGIN accepted 2,883,206, BEGIN rejected 92,323, CLEAR accepted 2,785,780, CLEAR rejected 548,446, COMMIT accepted 2,829,272, COMMIT rejected 181,965, EVENT accepted 2,165,524, EVENT rejected 91,977, FLUSH accepted 116,805, FLUSH rejected 99,415, OWN accepted 2,754,165, OWN rejected 162,311, READ_SELECT accepted 40,173,739, READ_SELECT rejected 144,982, ROUTE accepted 1,401,924, ROUTE rejected 92,665, SELECT accepted 58,977,649, SELECT rejected 144,312, START accepted 6,566,474, START rejected 999,854, STOP accepted 1,187,280, STOP rejected 91,467, TRIGGER accepted 768,338, TRIGGER rejected 198,717, invalid-op rejected 216,881

Host traffic: deselect (ena low) 3,382, engine reprogrammed 812,886, partial read abandoned (window 0) 811,419, partial read abandoned (window 3) 811,530, partial write abandoned (window 0) 540,201, partial write abandoned (window 1) 7,121,282, partial write abandoned (window 2) 540,858, revive: cleared fault 2,467,241, revive: restarted 3,753,508, rx read abandoned (empty) 3,161,628, rx word read 10,924,317, status read select=0 676,731, status read select=1 676,968, status read select=2 675,334, status read select=3 676,875, status read select=4 676,716, status read select=5 676,846, status read select=6 675,914, status read select=7 675,887, tx word accepted 6,898,331, tx write abandoned (full) 7,160,948

Other: cycles with IRQ asserted 3,009,262,763, cycles with any pin driven 1,534,321,420, cycles with fault output asserted 2,559,399,930, engine starts 6,340,482

Holes (bins never hit), all campaigns: `{"opcode_x_engine": {"0": [], "1": [], "2": [], "3": []}, "engines_without_any_completion": [], "stall_kinds": [], "fault_bins": [], "xfer_flag_combinations": [], "xfer_bit_counts": [], "host_commands": [], "host_traffic": [], "misc": [], "xcov": []}`

Holes, upstream generator only (`rtl-default`): `{"opcode_x_engine": {"0": [], "1": [], "2": [], "3": []}, "engines_without_any_completion": [], "stall_kinds": [], "fault_bins": [], "xfer_flag_combinations": [], "xfer_bit_counts": [], "host_commands": ["BEGIN rejected", "COMMIT rejected", "STOP rejected", "ROUTE rejected", "EVENT rejected"], "host_traffic": [], "misc": []}`

Failures: none

Extended cross coverage (`xcov.py`): hits per campaign, and hits per 1,000 cases in parentheses; sorted by the rarest rate over all xcov campaigns.

| bin | `rtl-xcov-default` (131,072 cases) | `rtl-xcov-dense` (16,384 cases) | `rtl-xcov-faulty` (16,384 cases) | `rtl-xcov-hostile` (16,384 cases) | `rtl-xcov-deselect` (16,384 cases) |
|---|---:|---:|---:|---:|---:|
| synchronous start of 4 engines | 47 (0.359) | 27 (1.65) | 9 (0.549) | 10 (0.61) | 2 (0.122) |
| mover blocked: host TX write to dest, same edge | 301 (2.3) | 67 (4.09) | 20 (1.22) | 49 (2.99) | 38 (2.32) |
| host TX write + engine PULL on same FIFO, same edge | 262 (2) | 159 (9.7) | 41 (2.5) | 79 (4.82) | 33 (2.01) |
| mover arbitration: >=2 routes eligible | 222 (1.69) | 682 (41.6) | 73 (4.46) | 209 (12.8) | 23 (1.4) |
| synchronous start of 3 engines | 643 (4.91) | 338 (20.6) | 102 (6.23) | 143 (8.73) | 77 (4.7) |
| reset/deselect with engines running | 320 (2.44) | 0 (0) | 0 (0) | 213 (13) | 1,475 (90) |
| WAITPIN/WAITEVENT timeout with LIMIT 1 | 1,469 (11.2) | 916 (55.9) | 458 (28) | 454 (27.7) | 164 (10) |
| mover push into FIFO the host writes next edge (dest == selected, window 2) | 2,727 (20.8) | 911 (55.6) | 252 (15.4) | 532 (32.5) | 347 (21.2) |
| mover push + engine PULL on same dest, same edge | 2,778 (21.2) | 1,254 (76.5) | 505 (30.8) | 774 (47.2) | 412 (25.1) |
| engines in XFER: 4 | 2,615 (20) | 3,345 (204) | 64 (3.91) | 407 (24.8) | 37 (2.26) |
| EVENT command and SIGNAL on same edge | 3,707 (28.3) | 748 (45.7) | 1,327 (81) | 1,120 (68.4) | 418 (25.5) |
| synchronous start of 2 engines | 4,217 (32.2) | 1,772 (108) | 790 (48.2) | 985 (60.1) | 478 (29.2) |
| FLUSH cleared an active route | 5,579 (42.6) | 3,647 (223) | 3,177 (194) | 2,288 (140) | 630 (38.5) |
| host RX pop + engine PUSH on same FIFO, same edge | 7,338 (56) | 4,957 (303) | 1,828 (112) | 2,251 (137) | 851 (51.9) |
| STOP/BEGIN of engine inside WAIT | 10,630 (81.1) | 2,520 (154) | 1,956 (119) | 2,556 (156) | 1,290 (78.7) |
| strict PUSH overflow (fault 4) while host holds that RX head | 8,533 (65.1) | 7,234 (442) | 2,328 (142) | 2,732 (167) | 1,007 (61.5) |
| mover pop + engine PUSH on same source, same edge | 13,076 (99.8) | 4,307 (263) | 1,728 (105) | 2,693 (164) | 1,628 (99.4) |
| reset/deselect inside XFER | 23,743 (181) | 3,119 (190) | 2,456 (150) | 2,656 (162) | 3,058 (187) |
| host fault flag cleared | 11,441 (87.3) | 10,460 (638) | 8,118 (495) | 7,637 (466) | 1,489 (90.9) |
| mover route exhausted (count reached 0) | 25,418 (194) | 9,012 (550) | 3,751 (229) | 5,545 (338) | 3,088 (188) |
| mover blocked: source RX head reserved by host read | 24,993 (191) | 15,795 (964) | 2,593 (158) | 8,834 (539) | 3,791 (231) |
| STOP/BEGIN of engine inside XFER | 35,662 (272) | 10,001 (610) | 6,796 (415) | 8,933 (545) | 4,145 (253) |
| ROUTE edited an active route | 33,790 (258) | 16,224 (990) | 15,131 (924) | 11,081 (676) | 4,135 (252) |
| host RX read abandoned mid-word (window change) | 31,531 (241) | 22,296 (1.36e+03) | 11,636 (710) | 11,798 (720) | 3,615 (221) |
| trigger delivered to engine blocked in WAITEVENT | 46,794 (357) | 10,384 (634) | 19,161 (1.17e+03) | 19,538 (1.19e+03) | 7,459 (455) |
| mover route to self moved a word | 56,270 (429) | 19,952 (1.22e+03) | 8,554 (522) | 12,383 (756) | 7,061 (431) |
| HALT with output enables active | 72,889 (556) | 20,219 (1.23e+03) | 35,940 (2.19e+03) | 20,005 (1.22e+03) | 8,884 (542) |
| host fault flag set | 122,942 (938) | 25,606 (1.56e+03) | 23,416 (1.43e+03) | 23,641 (1.44e+03) | 15,710 (959) |
| WAITEVENT satisfied after blocking | 103,399 (789) | 36,693 (2.24e+03) | 35,771 (2.18e+03) | 33,088 (2.02e+03) | 14,815 (904) |
| STOP/BEGIN of engine stalled on PULL/PUSH/WAITPIN/WAITEVENT | 172,169 (1.31e+03) | 47,694 (2.91e+03) | 30,688 (1.87e+03) | 44,972 (2.74e+03) | 19,908 (1.22e+03) |
| WAIT >= 20 cycles issued | 173,232 (1.32e+03) | 67,684 (4.13e+03) | 52,058 (3.18e+03) | 49,103 (3e+03) | 19,879 (1.21e+03) |
| engines in XFER: 3 | 188,487 (1.44e+03) | 106,495 (6.5e+03) | 21,414 (1.31e+03) | 39,882 (2.43e+03) | 24,202 (1.48e+03) |
| WAITEVENT consumed event with simultaneous new delivery | 184,026 (1.4e+03) | 41,717 (2.55e+03) | 67,206 (4.1e+03) | 64,954 (3.96e+03) | 22,607 (1.38e+03) |
| mover moved a word | 227,821 (1.74e+03) | 72,993 (4.46e+03) | 31,751 (1.94e+03) | 46,656 (2.85e+03) | 27,910 (1.7e+03) |
| fault with output enables active | 177,413 (1.35e+03) | 108,466 (6.62e+03) | 210,399 (1.28e+04) | 56,981 (3.48e+03) | 21,925 (1.34e+03) |
| XFER issued with half-period >= 4 | 293,884 (2.24e+03) | 106,955 (6.53e+03) | 96,912 (5.92e+03) | 90,366 (5.52e+03) | 33,763 (2.06e+03) |
| WAITEVENT satisfied on issue | 430,995 (3.29e+03) | 151,675 (9.26e+03) | 147,844 (9.02e+03) | 157,997 (9.64e+03) | 55,244 (3.37e+03) |
| WAITPIN satisfied after blocking | 679,052 (5.18e+03) | 174,131 (1.06e+04) | 226,880 (1.38e+04) | 231,384 (1.41e+04) | 83,074 (5.07e+03) |
| XFER issued with half-period 1 | 1,268,726 (9.68e+03) | 325,636 (1.99e+04) | 461,056 (2.81e+04) | 402,727 (2.46e+04) | 151,958 (9.27e+03) |
| mover arbitration: 4 routes configured | 357,682 (2.73e+03) | 903,057 (5.51e+04) | 991,931 (6.05e+04) | 535,095 (3.27e+04) | 29,993 (1.83e+03) |
| WAITPIN satisfied on issue | 1,821,826 (1.39e+04) | 369,575 (2.26e+04) | 644,363 (3.93e+04) | 605,824 (3.7e+04) | 222,715 (1.36e+04) |
| LOOP taken | 1,762,423 (1.34e+04) | 667,629 (4.07e+04) | 522,266 (3.19e+04) | 588,724 (3.59e+04) | 204,269 (1.25e+04) |
| LOOP fell through | 2,008,315 (1.53e+04) | 816,139 (4.98e+04) | 634,640 (3.87e+04) | 663,395 (4.05e+04) | 235,233 (1.44e+04) |
| JZ not taken | 2,289,261 (1.75e+04) | 777,522 (4.75e+04) | 718,495 (4.39e+04) | 765,684 (4.67e+04) | 282,566 (1.72e+04) |
| engines in XFER: 2 | 5,273,605 (4.02e+04) | 2,379,176 (1.45e+05) | 1,082,481 (6.61e+04) | 1,678,495 (1.02e+05) | 597,183 (3.64e+04) |
| trigger mode 0 (rise) detected | 8,470,508 (6.46e+04) | 3,188,563 (1.95e+05) | 2,824,726 (1.72e+05) | 2,812,725 (1.72e+05) | 1,008,537 (6.16e+04) |
| trigger mode 1 (fall) detected | 8,560,012 (6.53e+04) | 3,266,780 (1.99e+05) | 2,926,071 (1.79e+05) | 2,800,786 (1.71e+05) | 1,021,504 (6.23e+04) |
| engines running: 4 | 17,598,196 (1.34e+05) | 3,594,670 (2.19e+05) | 3,950,283 (2.41e+05) | 3,456,329 (2.11e+05) | 2,135,851 (1.3e+05) |
| IRQ from RX data only | 32,885,110 (2.51e+05) | 5,509,921 (3.36e+05) | 4,326,340 (2.64e+05) | 5,004,809 (3.05e+05) | 3,990,002 (2.44e+05) |
| open-drain pin released (logical 1) | 39,461,822 (3.01e+05) | 10,225,474 (6.24e+05) | 10,878,858 (6.64e+05) | 11,220,665 (6.85e+05) | 4,790,214 (2.92e+05) |
| JZ taken | 36,920,320 (2.82e+05) | 9,606,584 (5.86e+05) | 14,185,686 (8.66e+05) | 12,121,290 (7.4e+05) | 4,418,313 (2.7e+05) |
| open-drain pin pulled low | 52,532,685 (4.01e+05) | 15,457,293 (9.43e+05) | 14,000,074 (8.54e+05) | 14,663,577 (8.95e+05) | 6,268,330 (3.83e+05) |
| RX FIFO full e3 | 50,699,331 (3.87e+05) | 24,088,196 (1.47e+06) | 15,606,253 (9.53e+05) | 20,810,281 (1.27e+06) | 5,979,468 (3.65e+05) |
| RX FIFO full e2 | 53,825,219 (4.11e+05) | 24,932,342 (1.52e+06) | 16,731,608 (1.02e+06) | 20,900,061 (1.28e+06) | 6,268,953 (3.83e+05) |
| RX FIFO full e1 | 55,255,030 (4.22e+05) | 25,988,273 (1.59e+06) | 16,003,549 (9.77e+05) | 21,613,397 (1.32e+06) | 6,497,724 (3.97e+05) |
| trigger mode 2 (high) detected | 58,088,744 (4.43e+05) | 22,154,720 (1.35e+06) | 20,331,925 (1.24e+06) | 19,670,498 (1.2e+06) | 7,021,579 (4.29e+05) |
| RX FIFO full e0 | 57,645,694 (4.4e+05) | 27,115,215 (1.65e+06) | 15,302,851 (9.34e+05) | 21,828,531 (1.33e+06) | 6,744,426 (4.12e+05) |
| engines running: 3 | 66,712,145 (5.09e+05) | 18,215,359 (1.11e+06) | 20,765,576 (1.27e+06) | 19,165,214 (1.17e+06) | 7,954,028 (4.85e+05) |
| trigger mode 3 (low) detected | 61,214,954 (4.67e+05) | 24,315,017 (1.48e+06) | 21,841,040 (1.33e+06) | 20,276,390 (1.24e+06) | 7,518,028 (4.59e+05) |
| mover blocked: dest TX FIFO full | 59,331,939 (4.53e+05) | 52,046,984 (3.18e+06) | 32,132,001 (1.96e+06) | 35,968,191 (2.2e+06) | 6,857,647 (4.19e+05) |
| engines running: 2 | 113,592,242 (8.67e+05) | 42,192,672 (2.58e+06) | 44,466,262 (2.71e+06) | 43,429,614 (2.65e+06) | 13,402,523 (8.18e+05) |
| push-pull pin driven high | 125,077,947 (9.54e+05) | 41,297,078 (2.52e+06) | 48,041,284 (2.93e+06) | 43,975,115 (2.68e+06) | 15,227,001 (9.29e+05) |
| engines running: 1 | 118,708,122 (9.06e+05) | 57,073,110 (3.48e+06) | 50,130,391 (3.06e+06) | 51,345,603 (3.13e+06) | 14,693,102 (8.97e+05) |
| IRQ from event only | 152,859,891 (1.17e+06) | 32,564,745 (1.99e+06) | 52,654,183 (3.21e+06) | 39,619,266 (2.42e+06) | 19,754,990 (1.21e+06) |
| push-pull pin driven low | 160,159,416 (1.22e+06) | 61,285,185 (3.74e+06) | 59,804,667 (3.65e+06) | 57,088,764 (3.48e+06) | 19,031,121 (1.16e+06) |
| engines running: 0 | 192,072,175 (1.47e+06) | 57,737,719 (3.52e+06) | 42,534,077 (2.6e+06) | 43,389,253 (2.65e+06) | 25,321,675 (1.55e+06) |
| TX FIFO full e3 | 129,278,169 (9.86e+05) | 85,851,793 (5.24e+06) | 88,571,002 (5.41e+06) | 76,645,722 (4.68e+06) | 15,006,796 (9.16e+05) |
| TX FIFO full e2 | 133,516,002 (1.02e+06) | 87,143,728 (5.32e+06) | 89,120,354 (5.44e+06) | 76,911,057 (4.69e+06) | 15,380,031 (9.39e+05) |
| TX FIFO full e1 | 135,978,096 (1.04e+06) | 88,789,780 (5.42e+06) | 89,921,633 (5.49e+06) | 78,259,752 (4.78e+06) | 15,675,955 (9.57e+05) |
| TX FIFO full e0 | 140,163,486 (1.07e+06) | 88,208,019 (5.38e+06) | 89,640,298 (5.47e+06) | 77,286,504 (4.72e+06) | 16,573,710 (1.01e+06) |
| IRQ from event and RX data | 276,709,401 (2.11e+06) | 133,167,213 (8.13e+06) | 98,093,277 (5.99e+06) | 110,045,341 (6.72e+06) | 32,845,929 (2e+06) |

xcov observer errors: none
