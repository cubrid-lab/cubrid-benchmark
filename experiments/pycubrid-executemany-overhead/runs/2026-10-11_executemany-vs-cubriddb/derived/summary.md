# pycubrid executemany overhead: derived summary

## 1. End-to-end comparison of execution paths


#### SQL_LOG=ON (default): INSERT

Statement phase + commit, pooled median over all trials and iterations (ms). `stmt` excludes the commit. Ratio is total median / CUBRIDdb `executemany` total median.

| Rows | Path | total median | IQR | trial-median range | stmt median | commit median | ratio vs CUBRIDdb em | stmt ratio |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 100 | pycubrid executemany | 64.2 | 0.3 | 64.1-90.9 | 30.8 | 33.5 | 1.13x | 1.41x |
| 100 | CUBRIDdb executemany | 56.6 | 0.1 | 56.6-56.7 | 21.8 | 33.9 | 1.00x | 1.00x |
| 100 | pycubrid loop_execute | 97.5 | 10.9 | 97.1-246.1 | 64.2 | 33.1 | 1.72x | 2.94x |
| 100 | CUBRIDdb loop_execute | 81.6 | 0.1 | 81.6-81.7 | 49.2 | 32.3 | 1.44x | 2.26x |
| 100 | pycubrid batch_prerendered | 63.0 | 12.7 | 62.7-81.9 | 28.9 | 34.0 | 1.11x | 1.32x |
| 100 | pycubrid native_prepared | 64.2 | 46.4 | 56.0-112.7 | 28.2 | 33.3 | 1.13x | 1.29x |
| 1,000 | pycubrid executemany | 322.5 | 83.6 | 322.1-526.6 | 285.5 | 38.4 | 1.39x | 1.47x |
| 1,000 | CUBRIDdb executemany | 231.7 | 16.7 | 227.6-235.9 | 194.4 | 37.1 | 1.00x | 1.00x |
| 1,000 | pycubrid loop_execute | 672.5 | 97.9 | 647.9-4923.6 | 614.2 | 45.6 | 2.90x | 3.16x |
| 1,000 | CUBRIDdb loop_execute | 507.2 | 24.9 | 502.5-519.9 | 467.9 | 41.9 | 2.19x | 2.41x |
| 1,000 | pycubrid batch_prerendered | 339.3 | 74.3 | 311.1-465.7 | 279.9 | 40.1 | 1.46x | 1.44x |
| 1,000 | pycubrid native_prepared | 281.3 | 70.9 | 268.3-874.4 | 238.2 | 39.7 | 1.21x | 1.23x |
| 10,000 | pycubrid executemany | 3146.3 | 708.4 | 3063.4-4010.2 | 2946.5 | 193.3 | 1.54x | 1.60x |
| 10,000 | CUBRIDdb executemany | 2038.9 | 59.3 | 1997.5-2333.7 | 1844.1 | 184.0 | 1.00x | 1.00x |
| 10,000 | pycubrid loop_execute | 6489.0 | 3204.9 | 6330.0-44601.5 | 6304.3 | 181.8 | 3.18x | 3.42x |
| 10,000 | CUBRIDdb loop_execute | 4824.0 | 136.3 | 4714.0-4952.3 | 4647.5 | 173.7 | 2.37x | 2.52x |
| 10,000 | pycubrid batch_prerendered | 2972.5 | 302.0 | 2922.0-3945.0 | 2771.8 | 185.1 | 1.46x | 1.50x |
| 10,000 | pycubrid native_prepared | 2663.6 | 3545.0 | 2521.3-13117.2 | 2446.5 | 181.4 | 1.31x | 1.33x |

#### SQL_LOG=ON (default): UPDATE

Statement phase + commit, pooled median over all trials and iterations (ms). `stmt` excludes the commit. Ratio is total median / CUBRIDdb `executemany` total median.

| Rows | Path | total median | IQR | trial-median range | stmt median | commit median | ratio vs CUBRIDdb em | stmt ratio |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 100 | pycubrid executemany | 64.1 | 16.9 | 63.7-84.3 | 29.9 | 34.5 | 1.13x | 1.46x |
| 100 | CUBRIDdb executemany | 56.6 | 8.4 | 48.2-101.9 | 20.6 | 34.5 | 1.00x | 1.00x |
| 100 | pycubrid loop_execute | 89.0 | 44.2 | 80.6-163.4 | 50.9 | 32.4 | 1.57x | 2.48x |
| 100 | CUBRIDdb loop_execute | 81.6 | 8.4 | 81.5-131.5 | 48.8 | 33.7 | 1.44x | 2.37x |
| 100 | pycubrid batch_prerendered | 63.4 | 15.3 | 55.1-84.1 | 28.7 | 34.4 | 1.12x | 1.40x |
| 100 | pycubrid native_prepared | 55.8 | 32.6 | 55.5-100.6 | 23.3 | 32.5 | 0.99x | 1.13x |
| 1,000 | pycubrid executemany | 369.0 | 108.9 | 318.6-441.4 | 285.2 | 39.1 | 1.65x | 1.55x |
| 1,000 | CUBRIDdb executemany | 223.3 | 33.7 | 206.6-268.6 | 184.3 | 35.7 | 1.00x | 1.00x |
| 1,000 | pycubrid loop_execute | 639.0 | 422.8 | 551.5-1323.7 | 570.2 | 55.0 | 2.86x | 3.09x |
| 1,000 | CUBRIDdb loop_execute | 506.5 | 29.4 | 494.1-538.7 | 463.1 | 40.3 | 2.27x | 2.51x |
| 1,000 | pycubrid batch_prerendered | 334.9 | 90.3 | 306.3-448.1 | 284.3 | 36.2 | 1.50x | 1.54x |
| 1,000 | pycubrid native_prepared | 305.7 | 148.0 | 243.1-427.5 | 233.1 | 39.0 | 1.37x | 1.26x |
| 10,000 | pycubrid executemany | 2923.1 | 1517.2 | 2848.8-4669.1 | 2852.6 | 73.7 | 1.56x | 1.58x |
| 10,000 | CUBRIDdb executemany | 1873.5 | 83.0 | 1832.4-2159.3 | 1806.5 | 59.4 | 1.00x | 1.00x |
| 10,000 | pycubrid loop_execute | 6713.1 | 3394.1 | 4939.1-27349.3 | 6404.6 | 60.6 | 3.58x | 3.55x |
| 10,000 | CUBRIDdb loop_execute | 4715.4 | 183.8 | 4581.5-5570.4 | 4658.7 | 60.2 | 2.52x | 2.58x |
| 10,000 | pycubrid batch_prerendered | 3161.4 | 1156.3 | 2751.3-4251.9 | 3108.7 | 61.1 | 1.69x | 1.72x |
| 10,000 | pycubrid native_prepared | 2222.8 | 4690.4 | 2115.6-11201.8 | 2172.9 | 60.6 | 1.19x | 1.20x |

#### SQL_LOG=ON (default): DELETE

Statement phase + commit, pooled median over all trials and iterations (ms). `stmt` excludes the commit. Ratio is total median / CUBRIDdb `executemany` total median.

| Rows | Path | total median | IQR | trial-median range | stmt median | commit median | ratio vs CUBRIDdb em | stmt ratio |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 100 | pycubrid executemany | 64.1 | 10.8 | 55.7-86.0 | 29.4 | 34.4 | 1.13x | 1.49x |
| 100 | CUBRIDdb executemany | 56.5 | 8.4 | 48.2-80.2 | 19.7 | 33.4 | 1.00x | 1.00x |
| 100 | pycubrid loop_execute | 89.0 | 52.3 | 80.8-190.9 | 51.1 | 33.3 | 1.58x | 2.59x |
| 100 | CUBRIDdb loop_execute | 81.6 | 0.6 | 81.5-107.8 | 47.0 | 34.2 | 1.44x | 2.39x |
| 100 | pycubrid batch_prerendered | 63.6 | 18.4 | 63.3-84.1 | 29.5 | 35.2 | 1.13x | 1.50x |
| 100 | pycubrid native_prepared | 55.8 | 17.0 | 55.5-96.2 | 25.1 | 32.2 | 0.99x | 1.27x |
| 1,000 | pycubrid executemany | 330.6 | 119.3 | 305.6-461.9 | 284.3 | 39.6 | 1.48x | 1.57x |
| 1,000 | CUBRIDdb executemany | 223.2 | 36.6 | 214.9-242.2 | 181.3 | 37.9 | 1.00x | 1.00x |
| 1,000 | pycubrid loop_execute | 607.0 | 349.8 | 522.4-1000.9 | 547.2 | 48.8 | 2.72x | 3.02x |
| 1,000 | CUBRIDdb loop_execute | 498.2 | 29.2 | 485.8-511.0 | 450.7 | 41.8 | 2.23x | 2.49x |
| 1,000 | pycubrid batch_prerendered | 311.9 | 43.9 | 299.6-368.6 | 269.1 | 35.9 | 1.40x | 1.48x |
| 1,000 | pycubrid native_prepared | 247.5 | 59.1 | 235.9-301.8 | 209.5 | 38.0 | 1.11x | 1.16x |
| 10,000 | pycubrid executemany | 2888.2 | 67.7 | 2867.8-3007.5 | 2716.0 | 158.3 | 1.47x | 1.51x |
| 10,000 | CUBRIDdb executemany | 1959.4 | 618.8 | 1881.5-4765.7 | 1796.0 | 151.8 | 1.00x | 1.00x |
| 10,000 | pycubrid loop_execute | 4981.7 | 176.8 | 4931.3-5114.2 | 4812.7 | 145.2 | 2.54x | 2.68x |
| 10,000 | CUBRIDdb loop_execute | 4850.7 | 764.0 | 4541.2-5875.7 | 4716.9 | 149.0 | 2.48x | 2.63x |
| 10,000 | pycubrid batch_prerendered | 2821.4 | 464.8 | 2776.9-3300.0 | 2666.9 | 141.2 | 1.44x | 1.48x |
| 10,000 | pycubrid native_prepared | 2180.5 | 274.1 | 2080.7-2548.4 | 2023.5 | 145.8 | 1.11x | 1.13x |

#### SQL_LOG=ON (default): MERGE

Statement phase + commit, pooled median over all trials and iterations (ms). `stmt` excludes the commit. Ratio is total median / CUBRIDdb `executemany` total median.

| Rows | Path | total median | IQR | trial-median range | stmt median | commit median | ratio vs CUBRIDdb em | stmt ratio |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 100 | pycubrid executemany | 96.9 | 8.4 | 89.1-102.7 | 60.9 | 31.7 | 1.49x | 1.95x |
| 100 | CUBRIDdb executemany | 64.9 | 2.6 | 64.8-103.0 | 31.3 | 34.1 | 1.00x | 1.00x |
| 100 | pycubrid loop_execute | 126.2 | 8.5 | 122.1-158.9 | 92.9 | 33.8 | 1.94x | 2.97x |
| 100 | CUBRIDdb loop_execute | 115.0 | 8.8 | 106.7-195.3 | 82.8 | 33.1 | 1.77x | 2.65x |
| 100 | pycubrid batch_prerendered | 84.6 | 8.4 | 84.5-92.7 | 55.7 | 31.6 | 1.30x | 1.78x |
| 100 | pycubrid native_prepared | 72.2 | 8.3 | 64.1-72.3 | 37.4 | 33.5 | 1.11x | 1.20x |
| 1,000 | pycubrid executemany | 876.1 | 145.9 | 814.1-992.9 | 819.3 | 51.8 | 2.64x | 2.94x |
| 1,000 | CUBRIDdb executemany | 331.7 | 198.1 | 303.1-2087.4 | 278.3 | 40.1 | 1.00x | 1.00x |
| 1,000 | pycubrid loop_execute | 1268.4 | 333.5 | 1155.6-1698.0 | 1186.7 | 55.4 | 3.82x | 4.26x |
| 1,000 | CUBRIDdb loop_execute | 881.6 | 1742.7 | 840.5-3483.5 | 790.3 | 61.7 | 2.66x | 2.84x |
| 1,000 | pycubrid batch_prerendered | 794.3 | 91.3 | 773.8-865.0 | 735.9 | 51.1 | 2.39x | 2.64x |
| 1,000 | pycubrid native_prepared | 401.0 | 110.6 | 376.3-495.1 | 343.7 | 41.5 | 1.21x | 1.24x |
| 10,000 | pycubrid executemany | 13565.1 | 546.5 | 12963.7-17178.8 | 13412.8 | 159.9 | 4.72x | 5.04x |
| 10,000 | CUBRIDdb executemany | 2874.7 | 597.1 | 2739.8-14058.8 | 2662.9 | 163.5 | 1.00x | 1.00x |
| 10,000 | pycubrid loop_execute | 17524.8 | 7824.2 | 16871.4-25653.5 | 17460.9 | 156.4 | 6.10x | 6.56x |
| 10,000 | CUBRIDdb loop_execute | 8105.1 | 5778.9 | 7566.4-29117.7 | 7899.2 | 163.3 | 2.82x | 2.97x |
| 10,000 | pycubrid batch_prerendered | 13067.4 | 541.5 | 12543.3-13616.1 | 12893.0 | 164.2 | 4.55x | 4.84x |
| 10,000 | pycubrid native_prepared | 3371.9 | 307.5 | 3247.0-5554.8 | 3215.5 | 156.0 | 1.17x | 1.21x |

## 2. Client/server phase split (pycubrid, INSERT)


#### SQL_LOG=ON

Empty request (CHECK_CAS) round trip on this host: median 76 us (IQR 73-86 us, n=1400).

| Rows | Path | stmt ms | render params -> SQL | packet write | socket sendall | wait for reply (server+RTT) | receive rest | packet parse | residual (driver glue) | round trips | bytes out | commit ms |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | executemany | 30.5 | 1.2 (4%) | 0.1 (0%) | 0.0 (0%) | 29.0 (95%) | 0.0 (0%) | 0.1 (0%) | 0.1 (0%) | 2 | 9,807 | 34.1 |
| 100 | loop_execute | 64.2 | 2.1 (3%) | 1.4 (2%) | 3.0 (5%) | 50.3 (78%) | 0.4 (1%) | 1.2 (2%) | 5.4 (8%) | 300 | 19,568 | 33.0 |
| 100 | native_prepared | 28.6 | 0.0 (0%) | 1.4 (5%) | 1.2 (4%) | 19.7 (69%) | 0.2 (1%) | 0.8 (3%) | 3.3 (12%) | 102 | 14,980 | 34.3 |
| 1,000 | executemany | 288.3 | 11.9 (4%) | 0.6 (0%) | 0.1 (0%) | 274.7 (95%) | 0.0 (0%) | 0.8 (0%) | 0.4 (0%) | 2 | 100,710 | 37.9 |
| 1,000 | loop_execute | 622.9 | 20.8 (3%) | 14.1 (2%) | 30.0 (5%) | 490.9 (79%) | 4.0 (1%) | 11.9 (2%) | 52.4 (8%) | 3,000 | 198,671 | 45.3 |
| 1,000 | native_prepared | 252.0 | 0.0 (0%) | 13.0 (5%) | 10.8 (4%) | 184.5 (73%) | 1.5 (1%) | 7.4 (3%) | 30.3 (12%) | 1,002 | 150,882 | 43.1 |
| 10,000 | executemany | 2884.6 | 119.6 (4%) | 5.9 (0%) | 0.2 (0%) | 2747.7 (95%) | 0.0 (0%) | 8.3 (0%) | 3.1 (0%) | 2 | 1,036,713 | 205.9 |
| 10,000 | loop_execute | 6752.6 | 217.8 (3%) | 147.9 (2%) | 374.8 (6%) | 5315.9 (79%) | 42.1 (1%) | 124.9 (2%) | 552.9 (8%) | 30,000 | 2,016,674 | 179.1 |
| 10,000 | native_prepared | 2616.4 | 0.0 (0%) | 134.6 (5%) | 126.3 (5%) | 1927.4 (74%) | 15.6 (1%) | 76.8 (3%) | 309.1 (12%) | 10,002 | 1,527,884 | 196.6 |

Per-row view (microseconds per row, medians):

| Rows | Path | stmt us/row | render | packet write+parse | send+wait+recv (server+network) | residual | requests per row | us per request (send+wait+recv) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 100 | executemany | 305.4 | 12.2 | 1.6 | 290.8 | 1.2 | 0.02 | - |
| 100 | loop_execute | 642.4 | 20.9 | 26.3 | 536.6 | 54.0 | 3.00 | 179 |
| 100 | native_prepared | 285.9 | 0.0 | 21.7 | 210.2 | 33.2 | 1.02 | 206 |
| 1,000 | executemany | 288.3 | 11.9 | 1.4 | 274.8 | 0.4 | 0.00 | - |
| 1,000 | loop_execute | 622.9 | 20.8 | 26.0 | 524.8 | 52.4 | 3.00 | 175 |
| 1,000 | native_prepared | 252.0 | 0.0 | 20.5 | 196.8 | 30.3 | 1.00 | 196 |
| 10,000 | executemany | 288.5 | 12.0 | 1.4 | 274.8 | 0.3 | 0.00 | - |
| 10,000 | loop_execute | 675.3 | 21.8 | 27.3 | 573.3 | 55.3 | 3.00 | 191 |
| 10,000 | native_prepared | 261.6 | 0.0 | 21.1 | 206.9 | 30.9 | 1.00 | 207 |

### Broker SQL log (CAS side), per run

Slices of `broker1_*.sql.log` (SQL_LOG=ON, 1 ms timestamp resolution) between a unique marker statement and the disconnect. `server batch span` is the time between the `execute_batch N` and `execute_batch end` lines (one CAS processing N literal statements). For per-row paths the log has no usable per-statement time (all `time` fields are 0.000-0.001 s), so only the first-prepare-to-last-execute span is shown, which includes the client's think time between requests.

| Op | Rows | Path | client stmt ms | server batch span ms | prepare-to-last-exec span ms | prepares (plan-cache hit) | executes | batch stmts (plan-cache hit) | server commit ms |
|---|---:|---|---:|---:|---:|---|---:|---|---:|
| delete | 1,000 | cubriddb executemany | 247 | - | 247 | 2 (0) | 1000 | 0 (0) | 134 |
| delete | 1,000 | cubriddb loop_execute | 612 | - | 612 | 1001 (1000) | 1000 | 0 (0) | 65 |
| delete | 1,000 | pycubrid batch_prerendered | 273 | 272 | - | 1 (0) | 0 | 1000 (1000) | 34 |
| delete | 1,000 | pycubrid executemany | 625 | 611 | - | 1 (0) | 0 | 1000 (1000) | 74 |
| delete | 1,000 | pycubrid loop_execute | 1094 | - | 1094 | 1001 (1000) | 1000 | 0 (0) | 32 |
| delete | 1,000 | pycubrid native_prepared | 193 | - | 193 | 2 (1) | 1000 | 0 (0) | 38 |
| insert | 100 | cubriddb executemany | 21 | - | 20 | 2 (1) | 100 | 0 (0) | 69 |
| insert | 100 | cubriddb loop_execute | 49 | - | 49 | 101 (100) | 100 | 0 (0) | 32 |
| insert | 100 | pycubrid batch_prerendered | 28 | 28 | - | 1 (0) | 0 | 100 (100) | 25 |
| insert | 100 | pycubrid executemany | 34 | 32 | - | 1 (0) | 0 | 100 (100) | 68 |
| insert | 100 | pycubrid loop_execute | 60 | - | 60 | 101 (100) | 100 | 0 (0) | 27 |
| insert | 100 | pycubrid native_prepared | 24 | - | 24 | 2 (1) | 100 | 0 (0) | 29 |
| insert | 1,000 | cubriddb executemany | 191 | - | 191 | 2 (1) | 1000 | 0 (0) | 36 |
| insert | 1,000 | cubriddb loop_execute | 461 | - | 460 | 1001 (1000) | 1000 | 0 (0) | 78 |
| insert | 1,000 | pycubrid batch_prerendered | 275 | 272 | - | 1 (0) | 0 | 1000 (1000) | 35 |
| insert | 1,000 | pycubrid executemany | 287 | 273 | - | 1 (0) | 0 | 1000 (1000) | 37 |
| insert | 1,000 | pycubrid loop_execute | 602 | - | 602 | 1001 (1000) | 1000 | 0 (0) | 82 |
| insert | 1,000 | pycubrid native_prepared | 245 | - | 245 | 2 (1) | 1000 | 0 (0) | 73 |
| insert | 10,000 | cubriddb executemany | 1878 | - | 1878 | 2 (1) | 10000 | 0 (0) | 184 |
| insert | 10,000 | cubriddb loop_execute | 5338 | - | 5337 | 10001 (10000) | 10000 | 0 (0) | 162 |
| insert | 10,000 | pycubrid batch_prerendered | 2693 | 2678 | - | 1 (0) | 0 | 10000 (10000) | 272 |
| insert | 10,000 | pycubrid executemany | 2865 | 2734 | - | 1 (0) | 0 | 10000 (10000) | 170 |
| insert | 10,000 | pycubrid loop_execute | 6070 | - | 6070 | 10001 (10000) | 10000 | 0 (0) | 206 |
| insert | 10,000 | pycubrid native_prepared | 2290 | - | 2290 | 2 (1) | 10000 | 0 (0) | 179 |
| merge | 1,000 | cubriddb executemany | 364 | - | 364 | 2 (1) | 1000 | 0 (0) | 71 |
| merge | 1,000 | cubriddb loop_execute | 1045 | - | 1046 | 1001 (1000) | 1000 | 0 (0) | 52 |
| merge | 1,000 | pycubrid batch_prerendered | 781 | 779 | - | 1 (0) | 0 | 1000 (702) | 70 |
| merge | 1,000 | pycubrid executemany | 914 | 867 | - | 1 (0) | 0 | 1000 (548) | 91 |
| merge | 1,000 | pycubrid loop_execute | 1062 | - | 1062 | 1001 (746) | 1000 | 0 (0) | 36 |
| merge | 1,000 | pycubrid native_prepared | 379 | - | 378 | 2 (0) | 1000 | 0 (0) | 40 |
| update | 1,000 | cubriddb executemany | 239 | - | 239 | 2 (0) | 1000 | 0 (0) | 42 |
| update | 1,000 | cubriddb loop_execute | 640 | - | 640 | 1001 (1000) | 1000 | 0 (0) | 94 |
| update | 1,000 | pycubrid batch_prerendered | 317 | 316 | - | 1 (0) | 0 | 1000 (1000) | 35 |
| update | 1,000 | pycubrid executemany | 337 | 328 | - | 1 (0) | 0 | 1000 (1000) | 40 |
| update | 1,000 | pycubrid loop_execute | 679 | - | 679 | 1001 (1000) | 1000 | 0 (0) | 84 |
| update | 1,000 | pycubrid native_prepared | 243 | - | 243 | 2 (1) | 1000 | 0 (0) | 32 |

## 3. Client profile


### cProfile of `executemany` at 10,000 rows (client side)

3 runs x 3 profiled calls. Profiler overhead factor (profiled / unprofiled wall, median): 1.08x. Wall time inside the profiler is not representative; the *shares* are what matter. The full pstats listings are in `profile_cumulative.txt` and `profile_tottime.txt`.


Top by cumulative (first run, 3 calls accumulated):

```
         2490411 function calls (2430408 primitive calls) in 9.397 seconds

   Ordered by: cumulative time
   List reduced from 78 to 30 due to restriction <30>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        3    0.007    0.002    9.397    3.132 cursor.py:278(executemany)
        3    0.000    0.000    8.551    2.850 cursor.py:352(_executemany_batch)
        6    0.000    0.000    8.549    1.425 connection.py:906(_send_and_receive_locked)
        3    0.000    0.000    8.547    2.849 connection.py:865(_send_and_receive)
       12    0.000    0.000    8.357    0.696 connection.py:1102(_recv_exact)
       12    8.357    0.696    8.357    0.696 {method 'recv_into' of '_socket.socket' objects}
    30000    0.020    0.000    0.836    0.000 _cursor_common.py:550(_bind_parameters)
    30000    0.121    0.000    0.813    0.000 _cursor_common.py:461(bind_parameters)
    30000    0.322    0.000    0.347    0.000 _cursor_common.py:104(split_on_placeholders)
   120000    0.109    0.000    0.219    0.000 _cursor_common.py:336(format_parameter)
        3    0.006    0.002    0.103    0.034 protocol.py:1897(write)
    30000    0.027    0.000    0.096    0.000 packet.py:297(_write_null_terminated_string)
        3    0.034    0.011    0.088    0.029 protocol.py:1908(parse)
   630000    0.065    0.000    0.087    0.000 {built-in method builtins.issubclass}
120018/60018    0.024    0.000    0.081    0.000 {built-in method builtins.isinstance}
    30000    0.010    0.000    0.064    0.000 typing.py:1221(__instancecheck__)
    60000    0.045    0.000    0.060    0.000 _cursor_common.py:223(escape_string)
   420000    0.057    0.000    0.057    0.000 {method 'append' of 'list' objects}
    30000    0.014    0.000    0.054    0.000 typing.py:1492(__subclasscheck__)
    60009    0.015    0.000    0.023    0.000 packet.py:449(_parse_int)
```

Top by tottime (first run, 3 calls accumulated):

```
         2490411 function calls (2430408 primitive calls) in 9.397 seconds

   Ordered by: internal time
   List reduced from 78 to 30 due to restriction <30>

   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
       12    8.357    0.696    8.357    0.696 {method 'recv_into' of '_socket.socket' objects}
    30000    0.322    0.000    0.347    0.000 _cursor_common.py:104(split_on_placeholders)
    30000    0.121    0.000    0.813    0.000 _cursor_common.py:461(bind_parameters)
   120000    0.109    0.000    0.219    0.000 _cursor_common.py:336(format_parameter)
   630000    0.065    0.000    0.087    0.000 {built-in method builtins.issubclass}
   420000    0.057    0.000    0.057    0.000 {method 'append' of 'list' objects}
    60000    0.045    0.000    0.060    0.000 _cursor_common.py:223(escape_string)
        3    0.034    0.011    0.088    0.029 protocol.py:1908(parse)
    30000    0.027    0.000    0.096    0.000 packet.py:297(_write_null_terminated_string)
120018/60018    0.024    0.000    0.081    0.000 {built-in method builtins.isinstance}
    30000    0.020    0.000    0.836    0.000 _cursor_common.py:550(_bind_parameters)
   120009    0.017    0.000    0.017    0.000 {method 'unpack_from' of '_struct.Struct' objects}
    60009    0.015    0.000    0.023    0.000 packet.py:449(_parse_int)
    60000    0.014    0.000    0.023    0.000 packet.py:443(_parse_short)
    30000    0.014    0.000    0.054    0.000 typing.py:1492(__subclasscheck__)
    90018    0.012    0.000    0.012    0.000 {method 'extend' of 'bytearray' objects}
   120018    0.012    0.000    0.012    0.000 {built-in method builtins.len}
    30000    0.012    0.000    0.015    0.000 {built-in method _abc._abc_subclasscheck}
    30009    0.010    0.000    0.018    0.000 packet.py:259(_write_byte)
    30000    0.010    0.000    0.064    0.000 typing.py:1221(__instancecheck__)
```

## 4. Memory


### Memory (tracemalloc, Python heap)

| Rows | median peak | bytes per row | peaks |
|---:|---:|---:|---|
| 100 | 40,290 B | 403 | 40,418, 40,306, 40,290, 40,282, 40,274 |
| 1,000 | 354,637 B | 355 | 354,661, 354,653, 354,637, 354,629, 354,621 |
| 10,000 | 3,613,511 B | 361 | 3,613,527, 3,613,519, 3,613,511, 3,613,503, 3,613,495 |

Sample rendered SQL (102 chars): `INSERT INTO b822_ins (id, name, email, age) VALUES (10000, 'user_10000', 'user_10000@example.com', 20)`; `sys.getsizeof(str)` = 143 B, `sys.getsizeof(str.encode())` = 135 B.

Baseline 0 B, peak 3,627,302 B (363 B/row), still allocated after the call 130,792 B.


Top allocations, stage `after_render` (traced current 1,486,894 B = 149 B/row):

| allocation site | bytes | B/row | blocks |
|---|---:|---:|---:|
| `pycubrid/_cursor_common.py:485` | 1,396,682 | 139.7 | 10,000 |
| `pycubrid/cursor.py:319` | 85,176 | 8.5 | 2 |
| `pycubrid/_cursor_common.py:472` | 4,424 | 0.4 | 79 |
| `pycubrid/protocol.py:2093` | 168 | 0.0 | 2 |
| `/tmp/claude-1000/-data-GitHub-pycubrid/0a1784df-1111-416b-a8a3-bccbbb2734e8/scratchpad/inv822/publish/scripts/executemany_overhead_bench.py:539` | 120 | 0.0 | 3 |
| `pycubrid/cursor.py:321` | 112 | 0.0 | 2 |
| `pycubrid/_cursor_common.py:471` | 64 | 0.0 | 1 |
| `pycubrid/connection.py:989` | 61 | 0.0 | 2 |

Top allocations, stage `before_finalize` (traced current 2,583,033 B = 258 B/row):

| allocation site | bytes | B/row | blocks |
|---|---:|---:|---:|
| `pycubrid/_cursor_common.py:485` | 1,396,682 | 139.7 | 10,000 |
| `pycubrid/packet.py:278` | 1,089,424 | 108.9 | 1 |
| `pycubrid/cursor.py:319` | 85,176 | 8.5 | 2 |
| `pycubrid/_cursor_common.py:472` | 4,312 | 0.4 | 77 |
| `/tmp/claude-1000/-data-GitHub-pycubrid/0a1784df-1111-416b-a8a3-bccbbb2734e8/scratchpad/inv822/publish/scripts/executemany_overhead_bench.py:535` | 2,208 | 0.2 | 24 |
| `/home/yeongseon/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu/lib/python3.12/tracemalloc.py:173` | 2,115 | 0.2 | 11 |
| `/home/yeongseon/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu/lib/python3.12/tracemalloc.py:67` | 640 | 0.1 | 10 |
| `/home/yeongseon/.local/share/uv/python/cpython-3.12.13-linux-x86_64-gnu/lib/python3.12/tracemalloc.py:193` | 528 | 0.1 | 11 |

## 5. Sensitivity runs (INSERT)


#### broker SQL_LOG=OFF: INSERT

Statement phase + commit, pooled median over all trials and iterations (ms). `stmt` excludes the commit. Ratio is total median / CUBRIDdb `executemany` total median.

| Rows | Path | total median | IQR | trial-median range | stmt median | commit median | ratio vs CUBRIDdb em | stmt ratio |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 100 | pycubrid executemany | 69.8 | 126.8 | 63.6-195.2 | 35.8 | 33.3 | 0.91x | 1.69x |
| 100 | CUBRIDdb executemany | 76.8 | 95.1 | 48.3-139.2 | 21.1 | 36.0 | 1.00x | 1.00x |
| 100 | pycubrid loop_execute | 122.7 | 77.0 | 89.1-160.5 | 83.7 | 34.1 | 1.60x | 3.96x |
| 100 | CUBRIDdb loop_execute | 81.9 | 46.7 | 81.6-134.5 | 47.7 | 33.3 | 1.07x | 2.26x |
| 100 | pycubrid batch_prerendered | 69.7 | 26.7 | 62.7-159.7 | 32.8 | 34.4 | 0.91x | 1.55x |
| 100 | pycubrid native_prepared | 55.8 | 15.5 | 55.7-62.3 | 24.1 | 32.9 | 0.73x | 1.14x |
| 1,000 | pycubrid executemany | 353.7 | 48.5 | 331.4-378.7 | 314.3 | 37.5 | 1.54x | 1.84x |
| 1,000 | CUBRIDdb executemany | 229.8 | 106.5 | 197.6-565.2 | 171.1 | 41.2 | 1.00x | 1.00x |
| 1,000 | pycubrid loop_execute | 866.2 | 300.0 | 630.6-974.5 | 789.6 | 51.3 | 3.77x | 4.62x |
| 1,000 | CUBRIDdb loop_execute | 520.1 | 129.6 | 486.9-603.9 | 456.8 | 46.5 | 2.26x | 2.67x |
| 1,000 | pycubrid batch_prerendered | 356.6 | 59.2 | 316.7-391.0 | 310.8 | 38.3 | 1.55x | 1.82x |
| 1,000 | pycubrid native_prepared | 240.9 | 100.9 | 234.5-303.5 | 206.7 | 38.6 | 1.05x | 1.21x |
| 10,000 | pycubrid executemany | 3062.3 | 309.8 | 2983.6-3295.5 | 2813.9 | 208.5 | 1.73x | 1.83x |
| 10,000 | CUBRIDdb executemany | 1770.2 | 234.2 | 1704.3-1987.2 | 1541.5 | 197.6 | 1.00x | 1.00x |
| 10,000 | pycubrid loop_execute | 7651.3 | 1205.8 | 7443.9-8706.1 | 7481.5 | 181.2 | 4.32x | 4.85x |
| 10,000 | CUBRIDdb loop_execute | 4980.6 | 3561.4 | 4472.6-8786.8 | 4816.5 | 185.4 | 2.81x | 3.12x |
| 10,000 | pycubrid batch_prerendered | 2820.8 | 296.9 | 2785.4-2963.2 | 2624.1 | 175.4 | 1.59x | 1.70x |
| 10,000 | pycubrid native_prepared | 2154.1 | 378.4 | 2055.7-2312.7 | 1938.3 | 197.3 | 1.22x | 1.26x |

#### direct container IP (no docker-proxy): INSERT

Statement phase + commit, pooled median over all trials and iterations (ms). `stmt` excludes the commit. Ratio is total median / CUBRIDdb `executemany` total median.

| Rows | Path | total median | IQR | trial-median range | stmt median | commit median | ratio vs CUBRIDdb em | stmt ratio |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 100 | pycubrid executemany | 167.4 | 217.2 | 55.9-301.8 | 103.6 | 33.6 | 1.04x | 1.13x |
| 100 | CUBRIDdb executemany | 160.3 | 82.4 | 129.3-194.7 | 91.4 | 38.8 | 1.00x | 1.00x |
| 100 | pycubrid loop_execute | 164.7 | 222.0 | 97.2-328.7 | 110.9 | 33.3 | 1.03x | 1.21x |
| 100 | CUBRIDdb loop_execute | 164.4 | 207.5 | 81.6-197.0 | 68.0 | 37.2 | 1.03x | 0.74x |
| 100 | pycubrid batch_prerendered | 181.2 | 219.4 | 71.9-288.3 | 99.5 | 35.4 | 1.13x | 1.09x |
| 100 | pycubrid native_prepared | 153.6 | 112.5 | 71.7-191.0 | 97.6 | 34.9 | 0.96x | 1.07x |
| 1,000 | pycubrid executemany | 369.5 | 68.1 | 339.2-411.7 | 331.7 | 37.1 | 0.73x | 0.86x |
| 1,000 | CUBRIDdb executemany | 505.3 | 494.7 | 363.7-841.7 | 387.8 | 88.3 | 1.00x | 1.00x |
| 1,000 | pycubrid loop_execute | 984.7 | 444.6 | 672.5-1374.4 | 909.2 | 54.1 | 1.95x | 2.34x |
| 1,000 | CUBRIDdb loop_execute | 846.4 | 564.3 | 560.2-1030.4 | 733.1 | 66.7 | 1.68x | 1.89x |
| 1,000 | pycubrid batch_prerendered | 351.7 | 72.1 | 335.3-410.4 | 311.8 | 37.6 | 0.70x | 0.80x |
| 1,000 | pycubrid native_prepared | 275.1 | 81.2 | 255.8-381.4 | 234.4 | 37.0 | 0.54x | 0.60x |
| 10,000 | pycubrid executemany | 3537.9 | 553.0 | 3296.0-3852.7 | 3129.5 | 205.0 | 1.28x | 1.28x |
| 10,000 | CUBRIDdb executemany | 2764.4 | 865.5 | 2584.2-3477.7 | 2446.7 | 234.4 | 1.00x | 1.00x |
| 10,000 | pycubrid loop_execute | 10567.2 | 5763.4 | 6162.0-12604.4 | 10361.7 | 204.4 | 3.82x | 4.23x |
| 10,000 | CUBRIDdb loop_execute | 6135.7 | 3272.3 | 4693.2-8186.2 | 5603.9 | 200.5 | 2.22x | 2.29x |
| 10,000 | pycubrid batch_prerendered | 3110.0 | 666.4 | 2929.6-3666.7 | 2959.9 | 199.8 | 1.13x | 1.21x |
| 10,000 | pycubrid native_prepared | 3855.7 | 1577.4 | 2487.3-4242.7 | 3643.5 | 188.9 | 1.39x | 1.49x |

#### Phase split with SQL_LOG=OFF

Empty request (CHECK_CAS) round trip on this host: median 82 us (IQR 71-103 us, n=840).

| Rows | Path | stmt ms | render params -> SQL | packet write | socket sendall | wait for reply (server+RTT) | receive rest | packet parse | residual (driver glue) | round trips | bytes out | commit ms |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | executemany | 361.3 | 13.1 (4%) | 0.6 (0%) | 0.1 (0%) | 335.6 (93%) | 0.0 (0%) | 0.8 (0%) | 0.5 (0%) | 2 | 100,710 | 39.4 |
| 1,000 | loop_execute | 1096.7 | 31.9 (3%) | 22.2 (2%) | 57.9 (5%) | 592.0 (54%) | 5.0 (0%) | 19.1 (2%) | 87.7 (8%) | 3,000 | 198,671 | 44.4 |
| 1,000 | native_prepared | 225.5 | 0.0 (0%) | 13.8 (6%) | 12.8 (6%) | 143.0 (63%) | 1.5 (1%) | 7.8 (3%) | 32.5 (14%) | 1,002 | 150,882 | 36.2 |
| 10,000 | executemany | 3058.8 | 120.6 (4%) | 6.0 (0%) | 0.3 (0%) | 2901.8 (95%) | 0.0 (0%) | 8.3 (0%) | 3.3 (0%) | 2 | 1,036,713 | 177.5 |
| 10,000 | loop_execute | 6045.3 | 214.4 (4%) | 144.1 (2%) | 325.1 (5%) | 4661.5 (77%) | 39.3 (1%) | 119.2 (2%) | 531.1 (9%) | 30,000 | 2,016,674 | 160.6 |
| 10,000 | native_prepared | 2191.5 | 0.0 (0%) | 134.4 (6%) | 126.9 (6%) | 1496.0 (68%) | 15.0 (1%) | 74.8 (3%) | 311.7 (14%) | 10,002 | 1,527,884 | 171.1 |

Per-row view (microseconds per row, medians):

| Rows | Path | stmt us/row | render | packet write+parse | send+wait+recv (server+network) | residual | requests per row | us per request (send+wait+recv) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | executemany | 361.3 | 13.1 | 1.4 | 335.7 | 0.5 | 0.00 | - |
| 1,000 | loop_execute | 1096.7 | 31.9 | 41.4 | 654.9 | 87.7 | 3.00 | 218 |
| 1,000 | native_prepared | 225.5 | 0.0 | 21.6 | 157.3 | 32.5 | 1.00 | 157 |
| 10,000 | executemany | 305.9 | 12.1 | 1.4 | 290.2 | 0.3 | 0.00 | - |
| 10,000 | loop_execute | 604.5 | 21.4 | 26.3 | 502.6 | 53.1 | 3.00 | 168 |
| 10,000 | native_prepared | 219.1 | 0.0 | 20.9 | 163.8 | 31.2 | 1.00 | 164 |
