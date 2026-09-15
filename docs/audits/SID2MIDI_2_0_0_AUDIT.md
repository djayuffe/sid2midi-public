# sid2midi 2.0.0 Audit

Scope: `sid2midi.py`, `cpu6502.py`, tests, tools and validation scripts of the
1.x "NMOS CPU final perfect release", followed by batch conversion of 33 real
SID files. Each finding below is fixed in 2.0.0 and pinned by a test in
`tests/test_sid2midi_release.py` (test name in brackets).

## How findings were established

1. Full read of every source file.
2. Reproduction of each suspected defect with a minimal script against the 1.x code.
3. Batch conversion of 33 local PSID/RSID files (1.x: 1 crash, 5 timeouts).
4. Independent oracles: Bruce Clark's NMOS decimal-mode equations, the NMOS
   cycle table, the PSID v2NG header/banking specification, reSID envelope constants.

## Converter findings

| # | Severity | Finding | Test |
|---|----------|---------|------|
| C1 | Critical | `$C000-$CFFF` read from BASIC ROM → IndexError | `test_c000_block_is_ram_with_basic_banked_in` |
| C2 | Critical | No watchdog: runaway players hang for hours (4M instructions × every call) | `test_runaway_player_is_stopped_quickly` |
| C3 | High | PSID `$01` always `$37`; code under KERNAL runs ROM | `test_bank_selection_follows_psid_spec` |
| C4 | High | BRK in PSID play loops through KERNAL | `test_brk_stub_returns_instead_of_hanging` |
| C5 | High | IRQ tunes: fixed rate, both CIA and VIC flags raised, `$D012` += 1 per read | `test_rsid_cia_irq_rate_is_scheduled_from_timer`, `test_rsid_raster_irq_with_cia_disabled`, `test_raster_register_follows_cycle_time` |
| C6 | Medium | 2nd SID address `$D420\|b<<4`; no validation; no 3rd SID | `test_second_and_third_sid_addresses`, `test_multi_sid_voices_are_rendered` |
| C7 | Medium | Envelope stuck in attack after in-call gate pulse; wrong reSID rates | `test_gate_pulse_inside_one_call_releases`, `test_resid_rate_table` |
| C8 | Medium | `--auto` truncates to first period, losing intros | `test_loop_detection_keeps_intro` |
| C9 | Medium | CC74 clamped for top half of cutoff range | `test_cutoff_cc_uses_full_range` |
| C10 | Medium | Tone left sounding when voice switches to noise | `test_voice_switching_to_noise_releases_its_note` |
| C11 | Medium | Oversized data grows emulated RAM | `test_oversized_data_is_truncated_not_grown` |
| C12 | Low | Out-of-range notes wrapped by `& 0x7F` | `test_notes_outside_midi_range_are_dropped` |
| C13 | Low | Meta length written as one byte (invalid SMF > 127 bytes) | `test_long_meta_text_uses_variable_length` |
| C14 | Low | CC26 wraps instead of saturating | `test_digi_cc_saturates` |
| C15 | Low | No header validation; tracebacks for bad files | `test_rejects_garbage`, `test_header_repairs`, `test_all_songs_and_info_cli` |
| C16 | Low | I/O writes also stored in RAM under I/O; SID mirrors ignored | `test_io_writes_do_not_leak_into_ram_under_io`, `test_sid_mirrors_and_extra_sid_mapping` |
| C17 | Low | Duplicated load address in data not handled | `test_duplicate_load_address_fixup` |

## CPU findings

| # | Severity | Finding | Test |
|---|----------|---------|------|
| P1 | Medium | Base cycles wrong for `$0C $1C $2C $3C $4C $5C $6C $7C $DC $FC` (JMP abs 6 instead of 3) | `test_base_cycle_table_matches_nmos_reference` |
| P2 | Medium | Page-cross +1 on RMW abs,X; missing on LAX/LAS/NOP indexed reads | `test_page_cross_penalties` |
| P3 | Medium | Decimal ADC N flag, SBC N/Z flags, ARR decimal mode | `test_decimal_mode_matches_clark_for_every_input`, `test_lxa_xaa_arr_decimal` |
| P4 | Low | No RMW dummy write | `test_rmw_dummy_write` |
| P5 | Low | LXA/XAA executed as NOP | `test_lxa_xaa_arr_decimal` |
| P6 | Low | Class-level cycle table mutated by every instance | `test_cycle_table_is_per_instance` |
| P7 | Low | No budget/BRK status for callers | `test_brk_stop_and_budget_flags` |

## Validation and documentation findings

- 1.x tests covered only the CPU and a 1-note example; the converter had no tests.
- `validate_release.sh` required `sha256sum`; smoke output used fixed `/tmp` paths.
- Release documents claimed "100% closure" and "perfect" status that the
  findings above contradict; they are now marked historical.

## Remaining limitations (not defects)

NMI/CIA #2 playback, CIA timer B, ENV3 readback, BASIC-program RSIDs, MUS
files, SHX-family page-cross address corruption, sub-instruction timing.
