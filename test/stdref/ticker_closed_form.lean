-- Copyright (c) 2026 TeslaCoilerOW. SPDX-License-Identifier: Apache-2.0
import Mathlib

/-! Ticker schedule of docs/isa.md ("Ticker"): acc(0) = 0,
carry(k) = (acc(k) + Q) / 256, acc(k+1) = (acc(k) + Q) % 256,
T(k+1) = T(k) + P + carry(k).  Closed form used by
test/model/line_std_ref.py (ticker_tick_closed_form):
T(k) = T(0) + k*P + (k*Q)/256, and acc(k) = (k*Q) % 256. -/

def acc (Q : Nat) : Nat → Nat
  | 0 => 0
  | k + 1 => (acc Q k + Q) % 256

def tick (T0 P Q : Nat) : Nat → Nat
  | 0 => T0
  | k + 1 => tick T0 P Q k + P + (acc Q k + Q) / 256

theorem acc_closed (Q k : Nat) : acc Q k = (k * Q) % 256 := by
  induction k with
  | zero => simp [acc]
  | succ k ih =>
    simp only [acc, ih]
    rw [Nat.succ_mul]
    omega

theorem tick_closed (T0 P Q k : Nat) :
    tick T0 P Q k = T0 + k * P + (k * Q) / 256 := by
  induction k with
  | zero => simp [tick]
  | succ k ih =>
    simp only [tick, ih, acc_closed]
    rw [Nat.succ_mul, Nat.succ_mul]
    omega

