"""hil_gui.py – interface do PC.   Uso:  python hil_gui.py [app_pc.py]"""
import os
import sys
import time
import queue
import threading
import importlib.util
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from hil import Master


def load_app(path):
    path = os.path.abspath(path)
    sys.path.insert(0, os.path.dirname(path))
    spec = importlib.util.spec_from_file_location("hil_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class App(tk.Tk):
    N = 3000    # nº de amostras mais recentes exibidas / usadas nas estatísticas

    def __init__(self, app_path):
        super().__init__()
        self.hil_master= None
        self.title("HIL – PC ⇄ Raspberry Pi")
        self.app = load_app(app_path)
        self.cfg0 = self.app.CFG
        self.nu, self.ny = self.cfg0["n_pc2pi"], self.cfg0["n_pi2pc"]
        self.names_u = getattr(self.app, "NAMES_U", [f"u{i + 1}" for i in range(self.nu)])
        self.names_y = getattr(self.app, "NAMES_Y", [f"y{i + 1}" for i in range(self.ny)])
        self.stop = threading.Event()
        self.q = queue.Queue()
        self.last_log = None
        self.state = "idle"        # idle | waiting | ready | running
        self._build_ui()
        self._set_state("idle", "Pronto. Inicie o script da Pi e clique em “Aguardar Pi”.")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        left = ttk.Frame(self, padding=8)
        left.grid(row=0, column=0, sticky="ns")
        right = ttk.Frame(self)
        right.grid(row=0, column=1, sticky="nsew")
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        c = self.cfg0
        box = ttk.LabelFrame(left, text="Configuração", padding=6)
        box.pack(fill="x")
        fields = [("IP da Raspberry Pi", "pi_ip", c["pi"][0]),
                  ("Porta do PC", "pc_port", c["pc"][1]),
                  ("Porta da Pi", "pi_port", c["pi"][1]),
                  ("Passo dt [ms]", "dt_ms", c["dt"] * 1e3),
                  ("Duração [s]", "t_sim", c["t_sim"]),
                  ("Timeout/passo [ms]", "tmo_ms", c.get("timeout", 0.05) * 1e3)]
        self.v = {}
        for i, (lab, key, val) in enumerate(fields):
            ttk.Label(box, text=lab).grid(row=i, column=0, sticky="w")
            self.v[key] = tk.StringVar(value=val if isinstance(val, str) else f"{val:g}")
            ttk.Entry(box, textvariable=self.v[key], width=14).grid(row=i, column=1, padx=4, pady=1)
        ttk.Label(box, text=f"Pacotes: {self.nu} → Pi | {self.ny} ← Pi").grid(
            row=len(fields), column=0, columnspan=2, sticky="w", pady=(4, 0))

        bf = ttk.Frame(left)
        bf.pack(fill="x", pady=6)
        self.btn_wait = ttk.Button(bf, text="1. Aguardar Pi", command=self._on_wait)
        self.btn_start = ttk.Button(bf, text="2. Iniciar", command=self._on_start)
        self.btn_stop = ttk.Button(bf, text="Parar", command=self._on_stop)
        self.btn_post = ttk.Button(bf, text="Análise (post)", command=self._on_post)
        for b in (self.btn_wait, self.btn_start, self.btn_stop, self.btn_post):
            b.pack(fill="x", pady=1)
        self.live = tk.BooleanVar(value=True)
        ttk.Checkbutton(left, text="Gráfico ao vivo (desligue p/ medir latência\nsem interferência da interface)",
                        variable=self.live).pack(anchor="w")
        self.status = tk.StringVar()
        ttk.Label(left, textvariable=self.status, wraplength=240,
                  foreground="#0a6").pack(fill="x", pady=4)

        lf = ttk.LabelFrame(left, text="Sinais no gráfico", padding=4)
        lf.pack(fill="x")
        self.listbox = tk.Listbox(lf, selectmode=tk.MULTIPLE, height=8, exportselection=False)
        self.listbox.pack(fill="x")
        for n in self.names_u:
            self.listbox.insert(tk.END, f"u · {n}")
        for n in self.names_y:
            self.listbox.insert(tk.END, f"y · {n}")
        for i in range(min(3, self.ny)):
            self.listbox.selection_set(self.nu + i)

        sb = ttk.LabelFrame(left, text="Latência (últimas amostras)", padding=6)
        sb.pack(fill="x", pady=6)
        self.stat = {}
        items = [("rtt", "RTT atual"), ("mean", "RTT média"), ("p99", "RTT p99"),
                 ("max", "RTT máx"), ("net", "Rede (RTT − Pi)"), ("pi", "Cálculo na Pi"),
                 ("jit", "Jitter do período (σ)"), ("rate", "Taxa efetiva"),
                 ("cnt", "Perdidos / atrasados / overruns"), ("tim", "Tempo simulado")]
        for i, (k, lab) in enumerate(items):
            ttk.Label(sb, text=lab).grid(row=i, column=0, sticky="w")
            self.stat[k] = tk.StringVar(value="–")
            ttk.Label(sb, textvariable=self.stat[k], width=14, anchor="e").grid(row=i, column=1)

        self.fig = Figure(figsize=(9, 7), constrained_layout=True)
        gs = self.fig.add_gridspec(2, 2)
        self.ax_sig = self.fig.add_subplot(gs[0, :])
        self.ax_lat = self.fig.add_subplot(gs[1, 0])
        self.ax_hist = self.fig.add_subplot(gs[1, 1])
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    # ------------------------------------------------------------- estados
    def _set_state(self, state, text=None):
        prev, self.state = self.state, state
        if text:
            self.status.set(text)
        en = lambda b, f: b.config(state="normal" if f else "disabled")
        en(self.btn_wait, state == "idle")
        en(self.btn_start, state == "ready")
        en(self.btn_stop, state in ("waiting", "ready", "running"))
        en(self.btn_post, state == "idle" and self.last_log is not None
           and hasattr(self.app, "post"))
        if prev == "running" and state == "idle":
            self._refresh(force=True)

    def _cfg_from_fields(self):
        try:
            cfg = dict(self.cfg0)
            cfg["pi"] = (self.v["pi_ip"].get().strip(), int(self.v["pi_port"].get()))
            cfg["pc"] = (self.cfg0["pc"][0], int(self.v["pc_port"].get()))
            cfg["dt"] = float(self.v["dt_ms"].get()) / 1e3
            cfg["t_sim"] = float(self.v["t_sim"].get())
            cfg["timeout"] = float(self.v["tmo_ms"].get()) / 1e3
            return cfg
        except ValueError:
            messagebox.showerror("Configuração", "Confira os valores numéricos dos campos.")
            return None

    # ------------------------------------------------------------- botões
    def _on_wait(self):
        if getattr(self, "hil_master", None):
            self.hil_master.close()
            self.hil_master = None
        cfg = self._cfg_from_fields()
        if cfg is None:
            return
        if self.hil_master:
            self.hil_master.close()
        try:
            self.hil_master = Master(cfg)
        except OSError as e:
            messagebox.showerror("Rede", f"Não foi possível abrir a porta {cfg['pc'][1]}:\n{e}")
            return
        self.stop.clear()
        self._set_state("waiting", "Aguardando CONNECT da Pi...")
        threading.Thread(target=self._wait_worker, daemon=True).start()

    def _wait_worker(self):
        try:
            ok = self.hil_master.wait_pi(self.stop)
        except OSError:
            return
        self.q.put(("state", "ready" if ok else "idle",
                    "Pi conectada. Clique em “Iniciar”." if ok else "Espera cancelada."))

    def _on_start(self):
        cfg = self._cfg_from_fields()
        if cfg is None:
            return
        self.hil_master.cfg = cfg
        self.stop.clear()
        fname = time.strftime("log_hil_%Y%m%d_%H%M%S.npz")
        self._set_state("running", "Simulação em andamento...")
        threading.Thread(target=self._run_worker, args=(fname,), daemon=True).start()

    def _run_worker(self, fname):
        try:
            # Reinicia o estado da aplicação (planta/observador) a cada execução
            if hasattr(self.app, "reset"):
                self.app.reset()
            self.last_log = self.hil_master.run(
                self.app.source, sink=getattr(self.app, "sink", None),
                stop=self.stop, save_as=fname)
            self.q.put(("state", "idle", f"Concluído. Log salvo em {fname}"))
        except Exception as e:
            self.q.put(("state", "idle", f"Erro: {e!r}"))

    def _on_stop(self):
        self.stop.set()
        if self.state == "ready":
            self._set_state("idle", "Cancelado.")

    def _on_post(self):
        try:
            self.app.post(self.last_log)
            plt.show(block=False)
        except Exception as e:
            messagebox.showerror("Erro em post()", repr(e))

    def _on_close(self):
        self.stop.set()
        if self.hil_master:
            self.hil_master.close()
        self.destroy()

    # --------------------------------------------------------- atualização
    def _poll(self):
        try:
            while True:
                kind, *args = self.q.get_nowait()
                if kind == "state":
                    self._set_state(*args)
        except queue.Empty:
            pass
        if self.state == "running":
            self._refresh(force=False)
        self.after(300, self._poll)

    def _refresh(self, force):
        m = self.hil_master
        if m is None:
            return
        total = len(m.rows)
        rows = m.rows[-self.N:]           # fatiar lista é atômico -> seguro entre threads
        if len(rows) < 3:
            return
        t = np.array([r[0] for r in rows])
        rtt = np.array([r[3] for r in rows]) * 1e3
        tpi = np.array([r[4] for r in rows]) * 1e3
        net = rtt - tpi
        per = np.diff(t) * 1e3
        dt_ms = m.cfg["dt"] * 1e3

        s = self.stat
        s["rtt"].set(f"{rtt[-1]:.3f} ms")
        s["mean"].set(f"{rtt.mean():.3f} ms")
        s["p99"].set(f"{np.percentile(rtt, 99):.3f} ms")
        s["max"].set(f"{rtt.max():.3f} ms")
        s["net"].set(f"{net.mean():.3f} ms")
        s["pi"].set(f"{tpi.mean():.3f} ms")
        s["jit"].set(f"{per.std():.3f} ms")
        s["rate"].set(f"{total / max(t[-1], 1e-9):.0f} Hz")
        s["cnt"].set(f"{m.lost} / {m.stale} / {m.overruns}")
        s["tim"].set(f"{t[-1]:.2f} s")

        if not (self.live.get() or force):
            return
        u = np.array([r[1] for r in rows])
        y = np.array([r[2] for r in rows])

        ax = self.ax_sig
        ax.clear()
        for idx in self.listbox.curselection():
            if idx < self.nu:
                ax.plot(t, u[:, idx], "--", label=f"u · {self.names_u[idx]}")
            else:
                ax.plot(t, y[:, idx - self.nu], label=f"y · {self.names_y[idx - self.nu]}")
        ax.set_xlabel("t [s]"); ax.grid(True)
        if ax.get_lines():
            ax.legend(loc="upper right", ncol=3, fontsize=8)

        ax = self.ax_lat
        ax.clear()
        ax.plot(t, rtt, label="RTT total", lw=1)
        ax.plot(t, net, label="Rede", lw=1)
        ax.plot(t, tpi, label="Cálculo Pi", lw=1)
        ax.axhline(dt_ms, color="r", ls="--", lw=1, label="dt")
        ax.set_xlabel("t [s]"); ax.set_ylabel("ms"); ax.grid(True)
        ax.legend(fontsize=8)
        ax.set_title("Latência por passo")

        ax = self.ax_hist
        ax.clear()
        ax.hist(rtt, bins=40)
        ax.axvline(dt_ms, color="r", ls="--", lw=1)
        ax.set_xlabel("RTT [ms]"); ax.set_ylabel("nº de passos")
        ax.set_title("Distribuição do RTT (linha = dt)")
        self.canvas.draw_idle()


if __name__ == "__main__":
    App(sys.argv[1] if len(sys.argv) > 1 else "app_pc.py").mainloop()