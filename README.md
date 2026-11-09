# airline-write-gate

I wrote a small gate that checks each write an airline agent proposes (book, cancel, change flights, change bags, send a certificate) against the airline policy text, using only what the agent had read in the session by then. It says allow, hold or block, and for every hold or block it returns a reason and a repair the agent can act on. A passenger change is only checked for having read the reservation. Standard library only, pytest for development. It is written for Python 3.10 or newer; I ran it on 3.13.5 and Windows 11 only.

I replayed the 800 recorded airline runs of tau2-bench through it in shadow mode: every recorded write is judged before its own result is observed, then the run carries on exactly as recorded. This measures the gate. It says nothing about an agent that reads a block and tries again.

## Why

The airline policy in tau2-bench says twice that the API does not check the modification and cancellation rules for the agent, so the agent must (lines 113 and 149 of the airline `policy.md`). The benchmark's tools confirm it: `cancel_reservation` and `send_certificate` apply no policy rule, and `update_reservation_flights` checks availability, seats and the payment method but not whether the reservation may be changed. I wanted to know how many recorded writes a rule set written from the policy text would stop, and whether it stops writes the benchmark rewards.

## Run it

    git clone https://github.com/muhammad-musa-ml/airline-write-gate
    cd airline-write-gate
    python -m venv .venv && . .venv/bin/activate     # .venv\Scripts\activate on Windows
    pip install -e ".[dev]"
    python -m pytest
    python scripts/fetch_data.py data                # 36 MB, each file checked against its git blob hash
    airline-write-gate eval --data data --check      # fails if results/ differs from what the code produces
    python scripts/count_writes.py data              # counts the writes from the raw JSON, without importing the gate
    airline-write-gate replay data/gpt-4.1-2025-04-14_airline_default_gpt-4.1-2025-04-14_4trials.json --task 29 --trial 0

The last command prints this (a run the benchmark scored as passed; the arguments are cut at 100 characters by the command):

    task 29 trial 0 reward 1.0
      BLOCK update_reservation_flights (ran) {"reservation_id": "VA5SGQ", "cabin": "economy", "flights": [{"flight_number": "HAT169", "date": "20
            trip-endpoints [block]: VA5SGQ is round_trip DTW to LGA, and the new flights run DTW to DTW
            repair: keep the origin, destination and trip type; search for flights on the same route
      HOLD  update_reservation_baggages (ran) {"reservation_id": "VA5SGQ", "total_baggages": 1, "nonfree_baggages": 0, "payment_id": "credit_card_
            baggage-allowance [hold]: user raj_brown_5782 has not been read, so the free bag allowance is unknown
            repair: call get_user_details(user_id='raj_brown_5782') to learn the membership level

A block means the evidence in the session breaks a rule. A hold means a rule applies and the session has not read what it needs, so a hold is not a claim that the call was wrong.

## The rules

All ten are in `src/airline_write_gate/rules.py`; the numbers they use are in `policy.json`, including the clock, fixed at the policy's 2024-05-15 15:00.

| rule | what it checks | policy.md section |
|---|---|---|
| read-before-write | a write on a reservation or user the session never read is held | needed to check the rest |
| cancel-eligibility | no segment already flown (a segment dated today or earlier is held until its status is read), and one of: booked in the last 24 hours, business cabin, insurance, a flight the airline cancelled | Cancel flight |
| flight-change-basic-economy | the flights of a basic economy reservation do not change | Modify flight |
| cabin-change-after-flown | the cabin does not change after a segment was flown (same hold for an unread segment dated today or earlier) | Change cabin |
| trip-endpoints | a changed reservation keeps its origin, destination and trip type | Modify flight |
| flights-seen-available | a flight in the call was listed by a search in this session, with seats in the cabin; flights already on the reservation in the same cabin are exempt. The test for a status other than available never fires on these runs: `search_direct_flight` only returns available flights, and all 4,543 flights in the 1,200 recorded search results say available | evidence; the tool checks availability and seats too |
| baggage-no-removal | checked bags can be added, not removed | Change baggage |
| baggage-allowance | the non-free bag count matches the member level, cabin and passengers | Book flight |
| booking-limits | at most 5 passengers; at most one certificate, one credit card and three gift cards | Book flight |
| certificate-eligibility | silver or gold, insurance or business; 100 per passenger for a cancelled flight; 50 per passenger for a delayed one, only after the agent changed the flights of the reservation or cancelled it (a baggage or passenger update does not count) | Refunds and Compensation |

The tests cover each rule with the case it should block, the nearest case it should allow, and the boundary (a booking exactly 24 hours old and one second older, five passengers against six, a flight dated today and the day after). I also broke 40 lines of the code one at a time on a copy and required the suite to fail each time. 39 of them failed it. The 40th change, in `Session._store_reservation`, mattered only to a session built with reservations already in it, which no test used; I added that test and the change now fails the suite. The mutation script is not in this repo.

## Results

The first replay used the rules as committed at `fc60b6b47c2c8e82fbe21fb34f9be859b4099bdd`. Reading the code afterwards I found two defects in my own rules and fixed them in `2977163`:

- A segment dated before the policy clock was blocked as flown on the date alone. Among the 107 `get_flight_status` reads in the 800 runs for flights dated before the clock, 87 say landed, 15 delayed and 5 cancelled, and a cancelled flight was not flown. A segment dated today or earlier whose status the agent has not read is now a hold.
- The delayed-flight condition of the certificate rule never held after a real flights change, because the write result replaces the stored reservation and the delayed flight was no longer on it. The session now keeps every flight it has seen on a reservation.

I ran all four models again with the fixed rules, so the table below is the second run. Two models were development runs (gpt-4.1, o4-mini). The two held-out models (claude-3-7-sonnet, gpt-4.1-mini) were scored once at `fc60b6b` and again after the fix, so the second score is not a clean held-out score. Counts are writes that ran; a write that raised in the recorded run changed nothing and is counted apart in `results/results.json` (114 of the 1,008 recorded writes raised, 45 of them blocked).

| split | model | runs (passed / failed) | writes that ran | allow | hold | block | runs with a block (passed / failed) | blocks in passed runs also in later gold |
|---|---|---|---|---|---|---|---|---|
| development | gpt-4.1-2025-04-14 | 200 (112 / 88) | 205 | 125 | 57 | 23 | 6 / 17 | 0 of 6 |
| development | o4-mini-2025-04-16 | 200 (118 / 82) | 165 | 125 | 32 | 8 | 2 / 6 | 0 of 2 |
| held-out | claude-3-7-sonnet-20250219 | 200 (100 / 100) | 265 | 159 | 70 | 36 | 9 / 22 | 0 of 9 |
| held-out | gpt-4.1-mini-2025-04-14 | 200 (101 / 99) | 259 | 136 | 90 | 33 | 6 / 26 | 0 of 6 |

Of the 369 failed runs, 71 contain at least one write that ran and that the gate blocks. I did not check what else made each of them fail.

The last column needs the context. The recorded runs were scored in June 2025 against a task set that was revised in the tau2-bench 1.0.0 release ([commit `01e812d`](https://github.com/sierra-research/tau2-bench/commit/01e812d1d1c4df6d8d2299bf175e482527112244), 2026-03-18); the [results README at the pinned commit](https://github.com/sierra-research/tau2-bench/blob/19f13ea3f5fba8b4c4d272d1353590f29dc53062/data/tau2/results/final/README.md) says the airline tasks differ from the current ones. Every result file carries the task set it was scored against ("run gold"), and `tasks.json` at the pinned commit is the later one ("later gold"). 23 runs the benchmark scored as passed contain a block (6, 2, 9 and 6, in table order). In all 23, the blocked call is an expected action of the run gold and is not an expected action of the later gold. The 23 come from four tasks and repeated trials: task 2 (7 runs), 27 (5), 29 (6) and 38 (5). Before the fix the count was 28; the other five were cancellations of a reservation with segments dated in the past. The tasks where the later gold dropped a write, with what the gate said about that call in the 800 runs:

| task | write dropped from the later gold | later gold says | calls in the 800 runs | gate |
|---|---|---|---|---|
| 29 | change the flights of VA5SGQ (DTW-LGA) to DTW-JFK | "the reservation can't be changed and it can be cancelled instead" | 9 | 9 block |
| 2 | certificate, 50 | "the user does not want to change or cancel the reservation" | 13 | 7 block, 6 hold |
| 27 | certificate, 150 | "the user doesn't want to change or cancel the reservation" | 5 | 5 block |
| 38 | certificate, 50 | "Agent does not offer a $50 certificate" | 7 | 6 block, 1 hold |
| 9 | cancel NQNU5R | "Flights that have already departed cannot be cancelled" | 8 | 8 hold |
| 37 | cancel NQNU5R | "it is in the past" | 6 | 6 hold |
| 44 | cancel S61CZX | "as the user is healthy" | 13 | 13 allow |

Tasks 9 and 37 are holds because no agent had read the status of the two flights on NQNU5R (HAT214 on 2024-05-13, HAT045 on 2024-05-14) before it cancelled. The 800 runs hold 21 reads of those two flights' statuses, all landed (10 and 11), so a read would have turned the hold into a block, but no recorded run shows an agent doing that after a hold.

Task 44 is a miss: the reservation has insurance, which covers a health or weather reason, and the cancellation reason is in the chat, not in the call, so the gate does not check it. The other dropped or changed writes were not blocked either. Task 7 has a flight change (11 calls: 4 allow, 7 hold) whose later version differs only in the cabin, business where the run gold has economy. Task 29 also has a baggage update (8 calls: 4 allow, 4 hold), and task 33 a baggage update (4 calls, all hold). In tasks 23 and 35 the later gold drops a booking, and no call in the 800 runs matches the dropped one.

## What this does not show

- The split is by model, not by task. All four files hold the same 50 tasks. The 6 passed-run blocks from `trip-endpoints` (task 29) come from a rule I wrote before I replayed any run: the `rules.py` and `policy.json` committed at `d3af09e` give the same six blocks. That order is my account; the git history shows only commit times. The other 17 come from `certificate-eligibility`, which I wrote after I had seen which actions the later gold dropped, and I added its delayed-flight condition after reading the development results. Those 17 are not independent evidence. One edit to the cancellation rule also came after the first replay: a status seen in a search counts as read.
- A block is the gate's reading of the policy text. I did not re-score any run under the later gold, and I did not re-run any agent with the gate in its loop, so I cannot say a block would have turned a failed run into a passed one. In shadow mode the gate also keeps the results of writes it would have stopped, so a later verdict can rest on a state a gated agent would not have reached.
- A segment dated today or earlier is treated as flown only when its status was read as landed or flying. A past-dated segment read as delayed counts as not flown, which the policy text does not settle; 15 of the 107 past-dated status reads say delayed.
- The certificate rule takes any eligible reservation of the user that the session read, so it cannot tell which reservation the complaint is about.
- Holds are common: 249 writes in 187 runs, 35 of which passed. The largest group, 138 writes, is a cancellation whose flight statuses the agent never read.
- Not checked: the user's yes before a write, the cancellation reason, "do not offer compensation unless asked", and the price arithmetic of a change.
- One domain. The airline policy is short and its conditions are readable from the tool results; I have not tried retail or telecom.
- Timing: `Gate.evaluate` took 11.5 to 14.8 microseconds at the median and 38.3 to 50.1 at the 99th percentile over five passes of the 1,008 recorded writes, Python 3.13.5 on one Windows 11 machine (`results/bench.txt`). It does not include parsing a tool result, which happens when the result arrives. The earlier run, with the first version of the rules, gave 22.9 to 31.0 and 73.9 to 129.3, so treat the figures as machine-dependent.
- The GitHub workflow in `.github/workflows/ci.yml` and Python 3.10 have not been run by me.

tau2-bench is MIT licensed. `tests/fixtures/` holds trimmed excerpts of three of its recorded runs (tool calls and results only) and of its task entries; `scripts/make_fixtures.py` builds them. The repo is MIT licensed.
