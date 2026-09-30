# HIL Framework — PC ⇄ Raspberry Pi over UDP

A lightweight framework for **Hardware-in-the-Loop (HIL)** simulations between
a PC and a Raspberry Pi (or any other computer) connected over a local
network, with a monitoring GUI and latency measurement. It separates the
**communication infrastructure** (`hil.py`, `hil_gui.py`) from the **logic of
each application** (`cfg.py`, `app_pc.py`, `app_pi.py`), so that adapting the
framework to a new experiment means editing three small files, never the two
core ones.

> This project is an evolution of
> [DiogoFernandes-2008015/hil_rasp](https://github.com/DiogoFernandes-2008015/hil_rasp),
> a simpler, GUI-less set of Python scripts for hardware-in-the-loop
> communication with a Raspberry Pi. This repository adds a shared
> infrastructure module, a monitoring GUI with latency measurement, and a
> generic `source`/`step`/`sink`/`reset`/`post` contract so that new
> applications only require editing three small files.

```
your-repo/
├── hil.py          # communication infrastructure — do not edit
├── hil_gui.py       # PC-side GUI — do not edit
├── cfg.py           # YOUR application's configuration — edit
├── app_pc.py         # YOUR application's PC-side logic — edit
└── app_pi.py         # YOUR application's Pi-side logic — edit
```

---

## Contents

1. [Overview and architecture](#1-overview-and-architecture)
2. [Communication protocol](#2-communication-protocol)
3. [`hil.py` — infrastructure (do not edit)](#3-hilpy--infrastructure-do-not-edit)
4. [`hil_gui.py` — GUI (do not edit)](#4-hil_guipy--gui-do-not-edit)
5. [`cfg.py` — configuration (edit)](#5-cfgpy--configuration-edit)
6. [`app_pc.py` — PC-side application (edit)](#6-app_pcpy--pc-side-application-edit)
7. [`app_pi.py` — Pi-side application (edit)](#7-app_pipy--pi-side-application-edit)
8. [Running it](#8-running-it)
9. [Testing without the Raspberry Pi](#9-testing-without-the-raspberry-pi)
10. [Checklist: adapting to a new simulation](#10-checklist-adapting-to-a-new-simulation)
11. [Case studies: two full control-loop examples](#11-case-studies-two-full-control-loop-examples)
12. [Troubleshooting](#12-troubleshooting)
13. [Requirements](#13-requirements)

---

## 1. Overview and architecture

At every time step `dt`, the **PC** (`Master`) sends a packet of `n_pc2pi`
numbers to the **Pi** (`Slave`), which processes it and sends back a packet
of `n_pi2pc` numbers. The PC waits for the reply, logs the data, and moves on
to the next step. This cycle is generic: it can be a desired trajectory fed
to a kinematic controller, sensor readings fed to a state observer, or any
other (input → processing → output) pair.

```
     PC (Master)                              Raspberry Pi (Slave)
 ┌───────────────┐        CONNECT          ┌───────────────┐
 │               │ <──────────────────────  │               │
 │  hil_gui.py   │        START (dt)        │               │
 │      ⇅        │ ──────────────────────>  │   app_pi.py   │
 │  app_pc.py    │                          │      ⇅        │
 │  (source())   │  ── u (n_pc2pi) ────────> │   (step())    │
 │               │  <── y (n_pi2pc) ───────  │               │
 │               │        ... repeats every dt ...           │
 │               │        STOP              │               │
 │               │ ──────────────────────>  │               │
 └───────────────┘                          └───────────────┘
```

Only `cfg.py`, `app_pc.py`, and `app_pi.py` change from one experiment to
another. `hil.py` and `hil_gui.py` are generic and stay the same on both
sides (or PC-only, in the case of `hil_gui.py`).

---

## 2. Communication protocol

Implemented in `hil.py`, over plain UDP (no TCP, no network handshake beyond
what's described below). Every packet starts with a type byte:

| Type      | Value | Sender | Payload | When |
|-----------|:-----:|--------|---------|------|
| `CONNECT` | 1     | Pi     | — | repeated every 1 s until the PC replies |
| `START`   | 2     | PC     | `dt` (double) | once, when the simulation starts |
| `DATA`    | 3     | both   | sequence number + `n` doubles | every step |
| `STOP`    | 4     | PC     | — | at the end of the run, or when stopped |

Key points of the protocol:

- **`dt` lives in one place only:** it's set on the PC (`cfg.py` or the GUI)
  and sent to the Pi inside the `START` packet. This prevents the two sides
  from ending up with mismatched step sizes by mistake.
- **Every `DATA` packet carries a sequence number.** The Pi echoes back the
  same number it received. If a delayed reply arrives out of order, the PC
  discards it (counting it as "stale") instead of using it for the wrong
  step.
- **The Pi always appends, as the last value in the packet, the time it spent
  computing** (`step()`). This lets the PC separate how much of the total
  delay is network vs. processing — it's what feeds the latency monitor.
- **Packet size is fixed**, defined by `n_pc2pi` / `n_pi2pc` in `cfg.py`. Both
  sides must agree on these numbers, or deserialization fails.

---

## 3. `hil.py` — infrastructure (do not edit)

Provides two classes. Both expose a `.run(...)`, which is the only thing your
application needs to call.

### `Master` (runs on the PC)

| Method | Purpose |
|---|---|
| `Master(cfg)` | Opens the PC-side UDP socket, on port `cfg["pc"][1]`. |
| `.wait_pi(stop=None)` | Blocks until the Pi sends `CONNECT`. `stop` is an optional `threading.Event()` to cancel the wait (used by the GUI). |
| `.run(source, sink=None, stop=None, save_as=None)` | Runs the main loop: every `dt`, calls `source(t)` to get what to send, sends it to the Pi, waits for the reply (with a timeout), and, if `sink` was passed, calls `sink(t, u, y)` with what was sent and received. At the end, returns (and optionally saves to `.npz`) a log dictionary. |
| `.close()` | Closes the socket. |

The `log` returned by `.run()` contains:

```python
{
  "t":   array (N,)      # timestamp of each sample, in seconds
  "u":   array (N, n_pc2pi)   # what the PC sent
  "y":   array (N, n_pi2pc)   # what the Pi sent back
  "rtt": array (N,)      # round-trip time of each packet [s]
  "t_pi": array (N,)     # time the Pi spent computing that step [s]
  "dt", "lost", "stale", "overruns": scalars
}
```

`lost` counts packets with no reply within the `timeout`; `stale`, replies
that arrived out of order; `overruns`, steps where the PC's work (`source` +
waiting) took longer than the planned `dt`.

### `Slave` (runs on the Pi)

| Method | Purpose |
|---|---|
| `Slave(cfg)` | Opens the Pi-side UDP socket, on port `cfg["pi"][1]`. |
| `.run(step, reset=None, forever=True)` | Sends `CONNECT` repeatedly until it receives `START`; calls `reset(dt)`, if provided; then, for every `DATA` packet received, calls `step(u)` and sends back the result. On `STOP` (or if the PC stays silent for `slave_timeout` seconds), it goes back to the handshake — allowing several runs without restarting the Pi script. `forever=False` exits after a single cycle (useful for automated tests). |

### `set_realtime_priority(priority=80)`

Attempts to enable the `SCHED_FIFO` real-time scheduling policy on Linux
(applies to the Pi as well as to a Linux PC). Without root privileges, it
just prints a warning and continues normally — it never blocks execution.
Call it at the top of `app_pi.py`'s `if __name__ == "__main__":` block (and,
optionally, `app_pc.py`'s, if the PC also runs Linux).

> **You shouldn't need to edit `hil.py`** to adapt to a new application. Only
> touch it if you want to change the protocol itself (e.g. swap UDP for
> another transport).

---

## 4. `hil_gui.py` — GUI (do not edit)

A Tkinter interface that runs on the PC and drives `Master` under the hood.
Usage:

```bash
python hil_gui.py            # loads app_pc.py from the current folder
python hil_gui.py my_app.py  # loads a different application file
```

**What it automatically picks up** from whatever exists in your `app_pc.py`:

| From your `app_pc.py` | Used for |
|---|---|
| `CFG` | Populates the screen fields (IP, ports, `dt`, duration, timeout) |
| `NAMES_U`, `NAMES_Y` | Labels the signals in the chart's selection list |
| `source(t)` | Called at every step of the loop |
| `sink(t, u, y)` *(optional)* | Called at every step, if present — this is what lets a plant simulated on the PC evolve based on what the Pi returns |
| `reset()` *(optional)* | Called every time you click **Start**, before the first step — resets your application's internal state (initial position, step counter `k=0`, etc.) |
| `post(log)` *(optional)* | Called when you click **Analysis (post)**, to draw the final figures |

**Button flow:**

1. **1. Wait for Pi** — opens the socket and waits for the Pi's `CONNECT`
   (the Pi must already be running `app_pi.py`).
2. **2. Start** — sends `START`, calls `reset()` (if it exists), and starts the
   loop. The configuration fields (IP, `dt`, duration, timeout) are read **at
   this moment**, so you can adjust them between runs without restarting the
   GUI.
3. **Stop** — signals the loop to end as soon as possible.
4. **Analysis (post)** — calls your application's `post(log)` with the last
   run's log and displays the figures.

**Latency monitor**, fed by each sample's `rtt` and `t_pi`:

- Current RTT, mean, p99, max; period jitter; effective sample rate in Hz;
  lost/stale/overrun counters.
- Three plots: the signals selected in the list, RTT broken down into network
  vs. Pi-side computation over time, and the RTT histogram with a vertical
  line at the reference `dt`.

The **"Live chart"** checkbox turns off plot redrawing during the run (the
statistics keep updating) — important for rigorous latency measurements,
since redrawing consumes CPU time in the very process that's timing the
packets.

> **You shouldn't need to edit `hil_gui.py`.** If your application doesn't
> define `sink`, `reset`, or `post`, the GUI simply doesn't call them.

---

## 5. `cfg.py` — configuration (edit)

A single `CFG` dictionary, shared by the PC and the Pi — **copy the same file
to both sides** whenever you change something here.

```python
CFG = dict(
    pc=("192.168.1.50", 5005),   # PC's IP (where the Pi sends to) and the port the PC listens on
    pi=("192.168.1.100", 5006),  # Pi's static IP and the port the Pi listens on
    dt=0.001,                    # simulation step [s]
    t_sim=4.0,                   # duration of each run [s]
    timeout=0.05,                # max time to wait for a step's reply [s]
    slave_timeout=5.0,           # time the Pi waits in silence before returning to the handshake [s]
    n_pc2pi=6,                   # number of PC -> Pi variables
    n_pi2pc=3,                   # number of Pi -> PC variables
)
```

**Fields that almost always change between applications:**

| Field | What changes |
|---|---|
| `n_pc2pi`, `n_pi2pc` | Size of the vectors your application exchanges each step — must match what `source()` returns and what `step()` returns |
| `dt` | If your matrices/controller were discretized for a fixed period (e.g. 10 ms), `dt` **must** be exactly that value |
| `t_sim` | Duration of each run |
| `pc`, `pi` | Real IPs and ports on your network |

**Local loopback test mode, without the physical Pi** — append this at the
end:

```python
import os
if os.environ.get("HIL_LOCAL"):
    CFG["pc"] = ("127.0.0.1", 5005)
    CFG["pi"] = ("127.0.0.1", 5006)
```

See [section 9](#9-testing-without-the-raspberry-pi) for how to use it.

---

## 6. `app_pc.py` — PC-side application (edit)

This is the file you rewrite for each new experiment. Contract:

```python
from cfg import CFG                      # required: the GUI reads CFG from here

NAMES_U = [...]                          # optional but recommended
NAMES_Y = [...]                          # optional but recommended

def source(t):
    """t: elapsed time [s] since the run started.
    Returns a list/array with exactly CFG['n_pc2pi'] values."""
    ...

def sink(t, u, y):
    """Optional. Called every step, AFTER receiving the Pi's reply.
    u: what was sent this step (same value as source(t)).
    y: what the Pi sent back this step, already without the compute time.
    Use this to make a plant simulated on the PC evolve based on y."""
    ...

def reset():
    """Optional. Called by the GUI when you click 'Start',
    before the first step. Reset any internal state here
    (initial position, step counter, etc.)."""
    ...

def post(log):
    """Optional. Receives the run's log dictionary and draws the final
    figures with matplotlib. Do NOT call plt.show() here — both the
    GUI and the no-GUI mode take care of that on their own."""
    ...

if __name__ == "__main__":               # no-GUI mode
    from hil import Master, set_realtime_priority
    set_realtime_priority()
    m = Master(CFG)
    m.wait_pi()
    input("Pi connected. Press ENTER to start...")
    if "reset" in dir():
        reset()
    post(m.run(source, sink=sink if "sink" in dir() else None, save_as="log_hil.npz"))
    import matplotlib.pyplot as plt
    plt.show()
    m.close()
```

**What changes between applications:**

- The physics/math inside `source()` (and `sink()`, if there's a plant
  simulated on the PC).
- `NAMES_U` / `NAMES_Y` — names shown in the GUI, in the same order as the
  exchanged vectors.
- The figures drawn in `post()`.
- If your application does **not** have a plant simulated on the PC (for
  example, `source()` generates a ready-made reference trajectory, as in the
  inverse-kinematics example), you simply **don't define `sink`** — neither
  the GUI nor the no-GUI mode will complain about its absence.

> **Common mistake:** defining `sink` but forgetting to also define `reset`.
> If your simulated plant keeps state in an object created only once at
> module import time (`_plant = Plant()` at the top of the file), running the
> simulation a second time without `reset()` continues from the previous
> run's final state instead of starting over from zero.

---

## 7. `app_pi.py` — Pi-side application (edit)

An even simpler contract — usually a stateful class with two methods:

```python
from cfg import CFG

class Ctrl:
    def reset(self, dt):
        """Called every time a START is received from the PC. dt comes
        from the PC — don't redefine dt here, use the value you receive."""
        self.dt = dt
        ...   # reset the controller/observer's state here

    def step(self, u):
        """u: array with CFG['n_pc2pi'] values received from the PC.
        Returns a list/array with exactly CFG['n_pi2pc'] values."""
        ...
        return output

if __name__ == "__main__":
    from hil import Slave, set_realtime_priority
    set_realtime_priority()
    c = Ctrl()
    Slave(CFG).run(c.step, reset=c.reset)
```

**What changes between applications:** everything inside the class — the
controller model, the observer, or whatever other processing the Pi needs to
do each step. The signature of `step()` and `reset()` stays the same.

---

## 8. Running it

**Prerequisite:** static IPs configured on both sides, a direct Ethernet
cable (or a switch) between the PC and the Pi, and the `cfg.py` ports open in
the firewall.

1. Copy `hil.py` and `cfg.py` to both sides (identical on both).
2. On the Raspberry Pi:
   ```bash
   python3 app_pi.py
   ```
   It keeps sending `CONNECT` until the PC authorizes it.
3. On the PC:
   ```bash
   python hil_gui.py
   ```
   Click **1. Wait for Pi**, then **2. Start**.
4. When done, click **Analysis (post)** to see the figures, or open the
   `log_hil_YYYYMMDD_HHMMSS.npz` file that was saved automatically.

You can also run without the GUI: `python app_pc.py` on the PC (each file's
`if __name__ == "__main__":` block already handles this).

---

## 9. Testing without the Raspberry Pi

Useful for validating your `source`/`step`/`sink`/`post` logic before going
to the field. Just run both sides on the same computer, using `127.0.0.1`.

With the loopback snippet from [section 5](#5-cfgpy--configuration-edit)
already added to `cfg.py`, open two terminals in the project folder:

```bash
# Terminal A — plays the role of the Pi
HIL_LOCAL=1 python app_pi.py          # Linux/macOS
set HIL_LOCAL=1 && python app_pi.py   # Windows (cmd)
$env:HIL_LOCAL=1; python app_pi.py    # Windows (PowerShell)

# Terminal B — the PC, with the GUI (same environment variable)
HIL_LOCAL=1 python hil_gui.py
```

**What this test validates:** the `source`/`step`/`sink`/`post` logic, the
`n_pc2pi`/`n_pi2pc` match between the two sides, and the GUI's behavior.

**What it does NOT validate:** real network latency (in loopback, RTT stays
around 0.05–0.1 ms) or the Pi's actual compute time (which runs on your PC,
not on the target hardware). Use this test to debug your code; only draw
real-time conclusions with the physical Pi.

---

## 10. Checklist: adapting to a new simulation

When starting a new experiment, edit **only** these three files, in this
order:

- [ ] **`cfg.py`** — set `n_pc2pi`, `n_pi2pc`, `dt` (matching your
      model/controller's discretization period, if any), `t_sim`, `pc`, and
      `pi` (real IPs/ports). Copy the same file to both sides.
- [ ] **`app_pi.py`** — implement `step(u)` with your application's
      control/processing logic, and `reset(dt)` to zero out internal state.
      Make sure `step()` always returns exactly `n_pi2pc` values, even in
      edge cases.
- [ ] **`app_pc.py`** — implement `source(t)` returning exactly `n_pc2pi`
      values. If there's a plant or reference that evolves based on what the
      Pi returns, also implement `sink(t, u, y)` **and** `reset()` (both
      together — see the common mistake in section 6). Adjust
      `NAMES_U`/`NAMES_Y` and `post(log)` for the figures that make sense
      for this application.
- [ ] Run the [test without the Pi](#9-testing-without-the-raspberry-pi) first.
- [ ] Only then run with the physical Pi, starting with a short `t_sim`, and
      check the latency monitor to confirm `dt` is being respected (mean RTT
      well below `dt`, few or no overruns/lost packets).

---

## 11. Case studies: two full control-loop examples

### 11.1 14-state plant with LQR + observer

A more complex application example, illustrating the contract with a
state-space system whose full state is **not** directly measured:

- **`app_pc.py`** loads a discrete plant `x[k+1] = Ad·x[k] + Bd·u[k]` from
  matrices saved as `.csv` files (`Ad`, `Bd`, `Cnav`, `x0`). `source(t)`
  returns the current measurement `y = Cnav·x` (12 values); `sink(t, u, y)`
  applies the control received from the Pi and advances the plant one step;
  `reset()` restores `x = x0` and zeroes the step counter. Two disturbances
  are injected at fixed instants (defined **as a function of `CFG["dt"]`**,
  not as a fixed number of steps, so that changing `dt` doesn't shift the
  physical timing of the event).
- **`app_pi.py`** implements an LQR controller with a Luenberger observer:
  `step(u)` receives the 12 measurements, computes `u_control = -K·xhat`
  (using the state estimate **before** incorporating this step's
  measurement), and updates the observer with
  `xhat[k+1] = Aobs·xhat[k] + Bobs_u·u_control + Bobs_y·y`. `reset(dt)` zeroes
  `xhat` and stores the `dt` received from the PC.

This example highlights two precautions that apply to any application built
on pre-discretized matrices:

1. **`cfg.py`'s `dt` must be exactly the period used to generate
   `Ad`/`Bd`/`Aobs`, etc.** Changing `dt` in the GUI without regenerating the
   matrices produces incorrect dynamics, with no explicit error.
2. **Disturbances or events scheduled by step index** should be computed as
   `int(round(t_event / CFG["dt"]))`, not as a fixed constant — this way, if
   `dt` is ever adjusted, the physical timing of the event doesn't shift.

### 11.2 Inverted pendulum with state-feedback control (Euler-integrated plant)

A simpler, single-actuator example that shows a different way to build the
PC-side plant: instead of loading pre-discretized `Ad`/`Bd` matrices, it
keeps the plant in **continuous time** and integrates it on the fly with
`CFG["dt"]`.

- **`cfg.py`** sets `n_pc2pi=5` (4 states + 1 reference) and `n_pi2pc=1` (a
  single control force).
- **`app_pc.py`**'s `Plant` stores the continuous-time matrices `A` (4×4) and
  `B` (4×1) directly — there's no offline discretization step. `source(t)`
  sends the current state (`x`, `ẋ`, `θ`, `θ̇`) together with a reference
  `theta_ref` (from `get_theta_ref(t)`, currently a constant `0.0` — this is
  where you'd plug in a step, ramp, or any other setpoint profile). `sink(t,
  u, y)` receives the scalar control force from the Pi and integrates the
  plant forward one step with **explicit (forward) Euler**:
  `x ← x + (A·x + B·u) · dt`. `reset()` restores `x = x0`.
- **`app_pi.py`**'s `Ctrl` implements static state feedback with a
  precomputed gain vector `K` (e.g. from pole placement or LQR on the
  continuous-time model): `u = -K · [x, ẋ, (θ - θ_ref), θ̇]`, with the
  control force saturated to `±20 N` via `np.clip`. Since there's no
  integrator or observer state to carry between steps, this `Ctrl` doesn't
  even need a `reset(dt)` method — confirming that `reset` truly is optional
  on both sides of the contract.

This example illustrates a different trade-off from the LQR/observer case:

1. **Forward-Euler integration is only accurate for a small enough `dt`.**
   Unlike a plant built from pre-discretized matrices, here `dt` directly
   controls the numerical accuracy of the simulated dynamics, not just the
   communication rate. Shrinking `dt` improves accuracy but raises the
   network/timing burden on both sides — check the latency monitor to find a
   `dt` that is both numerically adequate and comfortably above the observed
   RTT.
2. **Sending the reference alongside the states** (`theta_ref` as the 5th
   value of `source(t)`) keeps the tracking setpoint entirely on the PC side,
   so `get_theta_ref(t)` is the single place to edit when changing what the
   pendulum should track — the Pi-side controller only ever computes an
   error, `theta - theta_ref`, without knowing where the reference comes
   from.
3. **Initial condition matters for a linearized model.** `x0` sets the
   state the linearization is valid around; if it starts far from that point
   (e.g. the pendulum hanging down instead of upright), a controller
   designed for the linearized-upright model may not behave as expected.
   Confirm `x0` matches the operating point your `A`, `B`, and `K` were
   derived for.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| In the GUI, values always come back as zero, but running `python app_pc.py` works fine | `app_pc.py` defines `sink`/`reset`, but an older version of the GUI wasn't calling them | Use the current version of `hil_gui.py` (it calls `reset()` before each run and passes `sink` to `Master.run`) |
| A second run in the same session starts with wrong values | Plant/object state created only once at import time, with `reset()` not implemented or not called | Implement `reset()` in `app_pc.py` and confirm the GUI is calling it |
| `ValueError: Pacote com tamanho inesperado` | `n_pc2pi`/`n_pi2pc` differ between the PC's and the Pi's `cfg.py` | Make sure both sides use the same `cfg.py` |
| Many "lost" packets in the latency monitor | `cfg.py`'s `timeout` smaller than the real network RTT, or `dt` smaller than the Pi can actually process | Increase `timeout`, or increase `dt`, or check the "Pi-side computation" plot to see where the bottleneck is |
| Trajectory/disturbance happens at the wrong instant when `dt` changes | Event scheduled as a fixed step count (`k == 200`) instead of as a function of time | Compute the index as `int(round(t_event / CFG["dt"]))` |
| Port-already-in-use error when clicking "Wait for Pi" | A previous instance of the script still has the port open | Close the previous instance or change the port in `cfg.py` |
| `ConnectionResetError` on Windows | ICMP "port unreachable" packet arrives when the other side hasn't opened its socket yet | Already handled in `hil.py`; if it persists, check that the Windows firewall isn't blocking the port |

---

## 13. Requirements

```
numpy
matplotlib
```

`tkinter` ships with most standard Python installations (on Linux you may
need `sudo apt install python3-tk`). No other external dependency is
required.
